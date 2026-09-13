"""A per-agent handoff contains one connection and references, never admin access."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import Profile, atomic_write
from .fleet import (
    REFERENCE_FIELDS,
    digest,
    identifier,
    name,
    validate_fields,
    validate_host,
    vault_values,
)
from .provision import Provisioner, operation_lock
from .security import guarded_query
from .snowflake import IDENTITY_SQL, SnowClient, timestamp
from .store import iso, utcnow
from .vault import Vault, credential_session, workload_session
from .workload import WORKLOAD_FIELDS, validate_workload


def export_bundle(fleet, identity: str) -> dict:
    provisioner = Provisioner(fleet)
    spec = provisioner.managed_spec(identity)
    state = fleet.state(identity)
    if not state or state.get("status") in {"revoked", "failed", "recovery_required"}:
        raise ValueError(
            "This identity has no ready runtime handoff. Resolve its lifecycle status first."
        )
    reference = state.get("pending_ref") or state.get("credential_ref")
    bundle = {
        "version": 1,
        "identity": identity,
        "target": fleet.target(spec),
        "runtime": spec["runtime"],
        "auth": spec["auth"],
        "profile": {"name": spec["connection"], "settings": provisioner._profile(spec).settings},
        "credential": reference,
        "credential_name": state.get("pending_name") or state.get("credential_name"),
        "fingerprint": state.get("pending_fingerprint") or state.get("public_key_fp"),
        "workload": state.get("workload"),
        "operation": state["operation_id"],
    }
    bundle["bundle_digest"] = digest(bundle)
    return bundle


def load_bundle(path: Path) -> dict:
    try:
        bundle = json.loads(path.read_text())
    except (ValueError, OSError):
        raise ValueError("Cannot read a valid Snowbeam runtime bundle.") from None
    fields = {
        "version",
        "identity",
        "target",
        "runtime",
        "auth",
        "profile",
        "credential",
        "credential_name",
        "fingerprint",
        "workload",
        "operation",
        "bundle_digest",
    }
    if not isinstance(bundle, dict) or set(bundle) != fields or bundle["version"] != 1:
        raise ValueError("Unsupported runtime bundle.")
    expected = digest({k: v for k, v in bundle.items() if k != "bundle_digest"})
    if bundle["bundle_digest"] != expected:
        raise ValueError(
            "The runtime bundle changed after export. Export it again from the manager."
        )
    name(bundle["identity"])
    if not isinstance(bundle["runtime"], str) or not bundle["runtime"]:
        raise ValueError("A runtime bundle requires a runtime label.")
    target = bundle["target"]
    if not isinstance(target, dict) or set(target) != {"organization", "account", "user"}:
        raise ValueError("Invalid runtime target.")
    for value in target.values():
        identifier(value)
    profile = bundle["profile"]
    if not isinstance(profile, dict) or set(profile) != {"name", "settings"}:
        raise ValueError("Invalid runtime profile.")
    settings = validate_fields(
        profile["settings"],
        {
            "account",
            "user",
            "role",
            "authenticator",
            "host",
            "warehouse",
            "workload_identity_provider",
            "token_file_path",
        },
    )
    name(profile["name"])
    if any(not isinstance(value, str) for value in settings.values()):
        raise ValueError("Runtime connection settings must be text.")
    identifier(settings.get("role"))
    if settings.get("host"):
        validate_host(settings["host"])
    if (
        settings.get("account") != target["organization"] + "-" + target["account"]
        or settings.get("user") != target["user"]
    ):
        raise ValueError("Runtime profile does not match the exported target.")
    if (
        bundle["auth"] not in {"PAT", "KEYPAIR", "WIF"}
        or settings.get("authenticator")
        != {
            "PAT": "PROGRAMMATIC_ACCESS_TOKEN",
            "KEYPAIR": "SNOWFLAKE_JWT",
            "WIF": "WORKLOAD_IDENTITY",
        }[bundle["auth"]]
    ):
        raise ValueError("Runtime authentication differs from the exported method.")
    if bundle["auth"] != "WIF":
        reference = validate_fields(bundle["credential"], REFERENCE_FIELDS)
        vault_values(reference)
        if reference.get("credential_kind") not in {None, bundle["auth"]}:
            raise ValueError("Vault reference has a different authentication method.")
        if "workload_identity_provider" in settings or "token_file_path" in settings:
            raise ValueError("Vault bundles must resolve their credential at runtime.")
    else:
        if bundle["credential"] is not None:
            raise ValueError("WIF must not include a vault credential.")
        binding = validate_workload(validate_fields(bundle["workload"], WORKLOAD_FIELDS))
        if settings.get("workload_identity_provider") != binding["workload_provider"]:
            raise ValueError("Runtime provider differs from the workload binding.")
        if settings.get("token_file_path") != binding.get("workload_token_file"):
            raise ValueError("Runtime token path differs from the workload binding.")
    return bundle


def session(bundle: dict):
    profile = Profile(
        bundle["profile"]["name"],
        Path("runtime-config.toml"),
        Path("runtime-config.toml"),
        bundle["profile"]["settings"],
    )
    return (
        workload_session(profile)
        if bundle["auth"] == "WIF"
        else credential_session(profile, Vault(bundle["credential"]).read())
    )


def test_bundle(bundle: dict, client: SnowClient) -> dict:
    target = bundle["target"]
    sql = (
        IDENTITY_SQL + ", CURRENT_IP_ADDRESS() AS CLIENT_IP, SYS_CONTEXT('SNOWFLAKE$SESSION', "
        "'IP_ADDRESS_V6') AS CLIENT_IPV6"
    )
    with session(bundle) as profile:
        rows = guarded_query(
            client, profile, target["organization"], target["account"], sql, user=target["user"]
        )
    observed = client._identity(rows)
    if observed.get("role_name") != bundle["profile"]["settings"].get("role"):
        raise ValueError("Runtime connected with an unexpected role.")
    allowed = {
        "organization_name",
        "account_name",
        "account_locator",
        "region",
        "user_name",
        "role_name",
        "warehouse_name",
        "client_ip",
        "client_ipv6",
    }
    return {
        "version": 1,
        "identity": bundle["identity"],
        "bundle_digest": bundle["bundle_digest"],
        "runtime": bundle["runtime"],
        "tested_at": iso(),
        "observed": {k: v for k, v in observed.items() if k in allowed},
        "source": "operator_supplied_runtime_report",
    }


def review_receipt(fleet, identity: str, path: Path) -> dict:
    try:
        receipt = json.loads(path.read_text())
    except (ValueError, OSError):
        raise ValueError("Cannot read the runtime test report.") from None
    if (
        not isinstance(receipt, dict)
        or set(receipt)
        != {"version", "identity", "bundle_digest", "runtime", "tested_at", "observed", "source"}
        or receipt["version"] != 1
    ):
        raise ValueError("Unsupported runtime test report.")
    bundle = export_bundle(fleet, identity)
    if (
        receipt["identity"] != identity
        or receipt["bundle_digest"] != bundle["bundle_digest"]
        or receipt["runtime"] != bundle["runtime"]
    ):
        raise ValueError(
            "The runtime report belongs to a different identity, credential, or setup revision."
        )
    tested = timestamp(receipt["tested_at"])
    if not tested:
        raise ValueError("The runtime report requires a timezone-aware test time.")
    age = utcnow() - datetime.fromisoformat(tested)
    if age > timedelta(hours=24) or age < -timedelta(minutes=5):
        raise ValueError("The runtime report is stale or dated in the future. Test again.")
    observed = receipt["observed"]
    if not isinstance(observed, dict) or set(observed) - {
        "organization_name",
        "account_name",
        "account_locator",
        "region",
        "user_name",
        "role_name",
        "warehouse_name",
        "client_ip",
        "client_ipv6",
    }:
        raise ValueError("Unexpected runtime observation fields.")
    if any(value is not None and not isinstance(value, str) for value in observed.values()):
        raise ValueError("Runtime observations must be text or null.")
    if receipt["source"] != "operator_supplied_runtime_report":
        raise ValueError("Unsupported runtime report source.")
    SnowClient._identity([observed])
    target = bundle["target"]
    if (
        any(observed.get(k + "_name") != target[k] for k in ("organization", "account", "user"))
        or observed.get("role_name") != bundle["profile"]["settings"]["role"]
    ):
        raise ValueError("The runtime report does not match the account, user, and role.")
    return {
        "receipt": receipt,
        "approval": digest(receipt),
        "notice": "This is an operator-supplied report, not signed Snowflake attestation. "
        "Approve only a report obtained from your trusted runtime.",
    }


def accept_receipt(fleet, identity: str, path: Path, approval: str) -> dict:
    with operation_lock(fleet.service.store.directory):
        reviewed = review_receipt(fleet, identity, path)
        if reviewed["approval"] != approval:
            raise ValueError("The runtime report changed or was not approved.")
        receipt = reviewed["receipt"]
        return Provisioner(fleet).record_verification(
            identity,
            receipt["observed"],
            receipt["runtime"],
            source="operator_supplied_runtime_report",
            verified_at=receipt["tested_at"],
        )


def write_json(path: Path, value: dict):
    if path.exists():
        raise ValueError("The output file already exists. Choose a new path.")
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=True) + "\n")
