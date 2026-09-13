import base64
import copy
import hashlib
import re

import pytest
from test_fleet import AUTH, NETWORK, agent

from snowbeam.provision import Provisioner
from snowbeam.snowflake import SnowClient, SnowError
from snowbeam.vault import VaultError


class Cloud:
    def __init__(self):
        self.users = {}
        self.calls = []
        self.policy = copy.deepcopy(AUTH)
        self.account = "PROD"
        self.fail_mint = False
        self.fail_cleanup = False
        self.vault_fails = False
        self.secrets = {}
        self.counter = 0
        self.vault_ready = True
        self.role = "ANALYST_AGENT"

    def identity(self, profile, **kwargs):
        user = profile.settings.get("user", "ALICE")
        return {
            "organization_name": "ACME",
            "account_name": self.account,
            "account_locator": "XY12345",
            "region": "AWS_US_EAST_1",
            "user_name": user,
            "role_name": self.role if user != "ALICE" else "SECURITYADMIN",
            "warehouse_name": "WH",
            "client_ip": "198.51.100.10",
        }

    _identity = staticmethod(SnowClient._identity)

    def execute(self, client, profile, org, account, sql, **kwargs):
        if (org, account) != ("ACME", self.account):
            raise SnowError("wrong_target", "Wrong account")
        if kwargs.get("user") and profile.settings["user"] != kwargs["user"]:
            raise SnowError("wrong_target", "Wrong user")
        self.calls.append(sql)
        words = sql.split()
        if sql.startswith("SHOW USERS"):
            return [{"name": key} for key in self.users]
        if sql.startswith("DESCRIBE AUTHENTICATION POLICY"):
            return [
                {"property": key, "value": value}
                for key, value in self.policy["properties"].items()
            ]
        if sql.startswith("DESCRIBE NETWORK POLICY"):
            return [{"name": key, "value": value} for key, value in NETWORK["properties"].items()]
        if sql.startswith("SHOW GRANTS TO ROLE"):
            return [{"privilege": "SELECT", "granted_on": "TABLE", "name": "DB.PUBLIC.ORDERS"}]
        if sql.startswith("CREATE USER"):
            user = words[2].strip('"')
            if user in self.users:
                raise SnowError("exists", "User exists")
            self.users[user] = {
                "disabled": True,
                "tokens": {},
                "auth": None,
                "network": None,
                "role": None,
            }
            return [{"status": "ok"}]
        if sql.startswith("GRANT ROLE"):
            self.users[words[-1].strip('"')]["role"] = words[2].strip('"')
            return [{"status": "ok"}]
        if sql.startswith("ALTER USER"):
            user = words[2].strip('"')
            data = self.users[user]
            if "ADD PROGRAMMATIC ACCESS TOKEN" in sql:
                token = words[7].strip('"')
                if token in data["tokens"]:
                    raise SnowError("exists", "Token exists")
                data["tokens"][token] = "SECRET_FOR_" + token
                if self.fail_mint:
                    raise SnowError("timeout", "Snowflake did not respond in time.")
                assert kwargs.get("sensitive") is True
                return [{"token_name": token, "token_secret": data["tokens"][token]}]
            if "REMOVE PROGRAMMATIC ACCESS TOKEN" in sql:
                if self.fail_cleanup:
                    raise SnowError("permission", "Cleanup permission unavailable.")
                data["tokens"].pop(words[-1].strip('"'), None)
            elif "AUTHENTICATION POLICY" in sql:
                data["auth"] = "SECURITY.POLICIES.PAT_ONLY"
            elif "NETWORK_POLICY" in sql:
                data["network"] = "RUNNER_NETWORK"
            elif " SET WORKLOAD_IDENTITY" in sql:
                arn = re.search(r"ARN = '([^']+)'", sql)
                if arn:
                    data["workload"] = {
                        "name": user,
                        "type": "AWS",
                        "additional_info": {
                            "awsPartition": "aws",
                            "awsAccount": "123456789012",
                            "type": "IAM_ROLE",
                            "iamRole": arn[1].split("role/", 1)[1],
                        },
                    }
            elif "UNSET WORKLOAD_IDENTITY" in sql:
                data.pop("workload", None)
            elif " SET RSA_PUBLIC_KEY" in sql:
                slot = words[4]
                encoded = re.search(r"= '([^']+)'", sql)[1]
                data[slot + "_FP"] = (
                    "SHA256:"
                    + base64.b64encode(hashlib.sha256(base64.b64decode(encoded)).digest()).decode()
                )
            elif "UNSET RSA_PUBLIC_KEY" in sql:
                data.pop(words[4] + "_FP", None)
            elif "DISABLED =" in sql:
                data["disabled"] = sql.endswith("TRUE")
            else:
                raise AssertionError(sql)
            return [{"status": "ok"}]
        if sql.startswith("DESCRIBE USER"):
            user = words[-1].strip('"')
            data = self.users[user]
            return [
                {"property": "NAME", "property_value": user},
                {"property": "TYPE", "property_value": "SERVICE_AGENT"},
                {"property": "DISABLED", "property_value": data["disabled"]},
            ] + [
                {"property": key, "property_value": value}
                for key, value in data.items()
                if key.endswith("_FP")
            ]
        if sql.startswith("SHOW USER WORKLOAD IDENTITY AUTHENTICATION METHODS"):
            data = self.users[words[-1].strip('"')]
            return [data["workload"]] if "workload" in data else []
        if sql.startswith("SHOW USER KEY PAIRS"):
            return []
        if sql.startswith("SHOW AUTHENTICATION POLICIES ON USER"):
            return [{"database_name": "SECURITY", "schema_name": "POLICIES", "name": "PAT_ONLY"}]
        if sql.startswith("SHOW PARAMETERS"):
            return [{"key": "NETWORK_POLICY", "value": "RUNNER_NETWORK", "level": "USER"}]
        if sql.startswith("SHOW GRANTS TO USER"):
            user = words[-1].strip('"')
            return [{"role": self.users[user]["role"], "grantee_name": user}]
        if sql.startswith("SHOW USER PROGRAMMATIC ACCESS TOKENS"):
            user = words[-1].strip('"')
            return [
                {
                    "user_name": user,
                    "name": key,
                    "status": "ACTIVE",
                    "role_restriction": "ANALYST_AGENT",
                    "expires_at": "2026-11-15T00:00:00+00:00",
                }
                for key in self.users[user]["tokens"]
            ]
        if sql.startswith("SELECT CURRENT_ORGANIZATION_NAME"):
            return [self.identity(profile)]
        raise AssertionError(sql)


@pytest.fixture
def setup(service, monkeypatch):
    cloud = Cloud()
    fleet = agent(service)
    service.client = cloud
    monkeypatch.setattr("snowbeam.security.guarded_query", cloud.execute)
    monkeypatch.setattr("snowbeam.provision.guarded_query", cloud.execute)

    class Vault:
        def __init__(self, spec):
            self.spec = spec

        def ready(self):
            if not cloud.vault_ready:
                raise VaultError("Vault is locked.")

        def create(self, title, secret):
            if cloud.vault_fails:
                raise VaultError("Vault write failed.")
            cloud.counter += 1
            key = f"00000000-0000-0000-0000-{cloud.counter:012}"
            cloud.secrets[key] = dict(secret)
            return {
                "provider": "onepassword",
                "vault": "agent-vault",
                "vault_account": "acme-account",
                "item": key,
            }

        def read(self):
            return cloud.secrets[self.spec["item"]]

    monkeypatch.setattr("snowbeam.provision.Vault", Vault)
    return fleet, cloud, Provisioner(fleet)


def apply(provisioner, action="provision"):
    plan = provisioner.plan("sam", action)
    return provisioner.apply("sam", action, plan["approval"])


def test_complete_pat_lifecycle_keeps_old_credential_until_verified(setup):
    fleet, cloud, p = setup
    plan = p.plan("sam")
    assert not cloud.users
    assert all(not c.startswith(("CREATE", "ALTER", "GRANT")) for c in cloud.calls)
    result = p.apply("sam", "provision", plan["approval"])
    assert result["status"] == "awaiting_verification"
    assert cloud.users["AGENT_SAM"]["disabled"] is False
    first = fleet.state("sam")["credential_name"]
    assert "SECRET_FOR" not in fleet.config.path.read_text()
    assert "SECRET_FOR" not in fleet.service.config.source.read_text()
    assert b"SECRET_FOR" not in fleet.service.store.path.read_bytes()
    verified = p.verify("sam", "runner-east")
    assert verified["role"] == "ANALYST_AGENT"
    assert fleet.state("sam")["status"] == "active"
    apply(p, "rotate")
    state = fleet.state("sam")
    second = state["pending_name"]
    assert second != first
    assert state["credential_name"] == first
    assert set(cloud.users["AGENT_SAM"]["tokens"]) == {first, second}
    with pytest.raises(ValueError, match="Verify"):
        p.plan("sam", "retire-old")
    p.verify("sam", "runner-east")
    assert fleet.state("sam")["previous_name"] == first
    apply(p, "retire-old")
    assert set(cloud.users["AGENT_SAM"]["tokens"]) == {second}
    apply(p, "revoke")
    assert not cloud.users["AGENT_SAM"]["tokens"]
    assert cloud.users["AGENT_SAM"]["disabled"] is True
    assert fleet.state("sam")["status"] == "revoked"
    assert len(fleet.service.store.operations()) == 4


def test_wrong_account_and_stale_approval_execute_no_mutations(setup):
    fleet, cloud, p = setup
    plan = p.plan("sam")
    fleet.config.save("identities", "sam", {"owner": "Bob"})
    with pytest.raises(ValueError, match="plan changed"):
        p.apply("sam", "provision", plan["approval"])
    assert not cloud.users
    cloud.account = "OTHER_CLIENT"
    with pytest.raises(ValueError, match="different organization"):
        p.plan("sam")
    assert not cloud.users


def test_policy_relaxation_and_existing_user_block_provisioning(setup):
    _, cloud, p = setup
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[ALL]"
    with pytest.raises(ValueError, match="only"):
        p.plan("sam")
    cloud.policy = copy.deepcopy(AUTH)
    cloud.users["AGENT_SAM"] = {}
    with pytest.raises(ValueError, match="already exists"):
        p.plan("sam")


def test_vault_failure_revokes_new_pat_and_leaves_user_disabled(setup):
    fleet, cloud, p = setup
    cloud.vault_fails = True
    with pytest.raises(ValueError, match="Vault write failed"):
        apply(p)
    assert not cloud.users["AGENT_SAM"]["tokens"]
    assert cloud.users["AGENT_SAM"]["disabled"] is True
    assert fleet.state("sam")["status"] == "recovery_required"
    assert "unsaved_pat_removed" in fleet.service.store.operations()[0]["steps"]
    assert not any(profile.name == "sam" for profile in fleet.service.config.profiles())


def test_uncertain_mint_and_failed_cleanup_are_visible(setup):
    fleet, cloud, p = setup
    cloud.fail_mint = True
    cloud.fail_cleanup = True
    with pytest.raises(ValueError, match="Operation stopped"):
        apply(p)
    assert fleet.state("sam")["status"] == "recovery_required"
    assert fleet.state("sam")["pending_name"] in cloud.users["AGENT_SAM"]["tokens"]
    assert "pat_cleanup_unconfirmed" in fleet.service.store.operations()[0]["steps"]
    assert fleet.service.store.operations()[0]["status"] == "recovery_required"


def test_locked_vault_never_creates_user_and_can_retry(setup):
    fleet, cloud, p = setup
    cloud.vault_ready = False
    with pytest.raises(ValueError, match="locked"):
        apply(p)
    assert not cloud.users
    assert not fleet.state("sam")
    cloud.vault_ready = True
    assert apply(p)["status"] == "awaiting_verification"


def test_runtime_verification_cannot_claim_wrong_role_or_runtime(setup):
    fleet, cloud, p = setup
    apply(p)
    with pytest.raises(ValueError, match="declared runtime"):
        p.verify("sam", "my-laptop")
    cloud.role = "SOME_OTHER_ROLE"
    with pytest.raises(ValueError, match="unexpected role"):
        p.verify("sam", "runner-east")
    assert fleet.state("sam")["status"] == "awaiting_verification"


def test_target_cannot_be_repointed_after_provisioning(setup):
    fleet, _, p = setup
    apply(p)
    fleet.config.save("templates", "reporting", {"account": "OTHER"})
    with pytest.raises(ValueError, match="different identity or account"):
        p.plan("sam", "revoke")


def test_key_pair_rotation_preserves_owned_slots_until_verified(setup, monkeypatch):
    fleet, cloud, p = setup
    fleet.config.save("templates", "reporting", {"auth": "KEYPAIR"})
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[KEYPAIR]"
    counter = 0

    def key():
        nonlocal counter
        counter += 1
        raw = f"PUBLIC_{counter}".encode()
        return (
            {"private_key": f"ENCRYPTED_TEST_KEY_{counter}", "passphrase": "TEST_PASSPHRASE"},
            base64.b64encode(raw).decode(),
            "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode(),
        )

    monkeypatch.setattr("snowbeam.provision.generate_key", key)
    apply(p)
    p.verify("sam", "runner-east")
    first = fleet.state("sam")["public_key_fp"]
    assert fleet.state("sam")["key_slot"] == "RSA_PUBLIC_KEY"
    apply(p, "rotate")
    assert cloud.users["AGENT_SAM"]["RSA_PUBLIC_KEY_FP"] == first
    assert "RSA_PUBLIC_KEY_2_FP" in cloud.users["AGENT_SAM"]
    p.verify("sam", "runner-east")
    apply(p, "retire-old")
    assert "RSA_PUBLIC_KEY_FP" not in cloud.users["AGENT_SAM"]
    assert "RSA_PUBLIC_KEY_2_FP" in cloud.users["AGENT_SAM"]
    apply(p, "revoke")
    assert "RSA_PUBLIC_KEY_2_FP" not in cloud.users["AGENT_SAM"]


def test_existing_unmanaged_public_key_is_not_overwritten_or_removed(setup, monkeypatch):
    fleet, cloud, p = setup
    fleet.config.save("templates", "reporting", {"auth": "KEYPAIR"})
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[KEYPAIR]"
    raw = b"PUBLIC"
    fp = "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode()
    monkeypatch.setattr(
        "snowbeam.provision.generate_key",
        lambda: (
            {"private_key": "ENCRYPTED_TEST_KEY", "passphrase": "TEST"},
            base64.b64encode(raw).decode(),
            fp,
        ),
    )
    apply(p)
    p.verify("sam", "runner-east")
    cloud.users["AGENT_SAM"]["RSA_PUBLIC_KEY_2_FP"] = "UNMANAGED_KEY"
    with pytest.raises(ValueError, match="Both"):
        p.plan("sam", "rotate")
    cloud.users["AGENT_SAM"]["RSA_PUBLIC_KEY_FP"] = "REPLACED_OUTSIDE_SNOWBEAM"
    with pytest.raises(ValueError, match="changed outside"):
        p.plan("sam", "revoke")


def test_workload_provisioning_uses_unique_cloud_identity_and_no_vault(setup):
    from test_fleet import TEMPLATE

    fleet, cloud, p = setup
    template = {
        k: v for k, v in TEMPLATE.items() if k not in {"provider", "vault", "vault_account"}
    }
    template["auth"] = "WIF"
    fleet.config.save("templates", "cloud", template, create=True)
    fleet.config.add_agent(
        "cloudsam",
        "cloud",
        "CLOUD_SAM",
        "cloudsam",
        owner="Alice",
        runtime="cloud-runner",
        workload_provider="AWS",
        workload_subject="arn:aws:iam::123456789012:role/sam",
    )
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[WORKLOAD_IDENTITY]"
    cloud.vault_ready = False
    plan = p.plan("cloudsam")
    assert plan["workload"]["workload_subject"].endswith("role/sam")
    assert plan["vault"] == {}
    p.apply("cloudsam", "provision", plan["approval"])
    assert not cloud.secrets
    assert fleet.state("cloudsam")["credential_kind"] == "WIF"
    p.verify("cloudsam", "cloud-runner")
    assert fleet.state("cloudsam")["status"] == "active"
    with pytest.raises(ValueError, match="provider renews"):
        p.plan("cloudsam", "rotate")
    with pytest.raises(ValueError, match="already assigned"):
        fleet.config.add_agent(
            "other",
            "cloud",
            "OTHER",
            "other",
            workload_provider="AWS",
            workload_subject="arn:aws:iam::123456789012:role/sam",
        )
    plan = p.plan("cloudsam", "revoke")
    p.apply("cloudsam", "revoke", plan["approval"])
    assert cloud.users["CLOUD_SAM"]["disabled"] is True
    assert "workload" not in cloud.users["CLOUD_SAM"]


def test_runtime_handoff_excludes_manager_access_and_accepts_only_reviewed_report(
    setup, monkeypatch, tmp_path
):
    import json

    from snowbeam.runtime import (
        accept_receipt,
        export_bundle,
        load_bundle,
        review_receipt,
        write_json,
    )
    from snowbeam.runtime import (
        test_bundle as run_test,
    )

    fleet, cloud, p = setup
    apply(p)
    p.verify("sam", "runner-east")
    old = fleet.state("sam")["credential_name"]
    apply(p, "rotate")
    monkeypatch.setattr("snowbeam.runtime.guarded_query", cloud.execute)
    monkeypatch.setattr(
        "snowbeam.runtime.Vault", __import__("snowbeam.provision", fromlist=["Vault"]).Vault
    )
    bundle = export_bundle(fleet, "sam")
    payload = json.dumps(bundle)
    assert "SECRET_FOR" not in payload
    assert "admin_connection" not in payload
    assert "ALICE" not in payload
    assert "previous_ref" not in payload
    path = tmp_path / "bundle.json"
    write_json(path, bundle)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already exists"):
        write_json(path, bundle)
    receipt = run_test(load_bundle(path), cloud)
    report = tmp_path / "report.json"
    write_json(report, receipt)
    review = review_receipt(fleet, "sam", report)
    assert "operator-supplied" in review["notice"]
    assert fleet.state("sam")["status"] == "awaiting_verification"
    with pytest.raises(ValueError, match="not approved"):
        accept_receipt(fleet, "sam", report, "not-reviewed")
    accept_receipt(fleet, "sam", report, review["approval"])
    state = fleet.state("sam")
    assert state["previous_name"] == old
    assert state["verification_source"] == "operator_supplied_runtime_report"
    assert set(cloud.users["AGENT_SAM"]["tokens"]) == {old, state["credential_name"]}
    # The old report cannot approve another credential generation.
    apply(p, "retire-old")
    apply(p, "rotate")
    with pytest.raises(ValueError, match="revision"):
        review_receipt(fleet, "sam", report)


@pytest.mark.parametrize("change", ["user", "role", "stale", "future", "fields", "source"])
def test_runtime_receipt_rejects_wrong_or_stale_evidence(setup, tmp_path, change):
    import json
    from datetime import timedelta

    from snowbeam.runtime import export_bundle, review_receipt
    from snowbeam.store import iso, utcnow

    fleet, cloud, p = setup
    apply(p)
    bundle = export_bundle(fleet, "sam")
    receipt = {
        "version": 1,
        "identity": "sam",
        "bundle_digest": bundle["bundle_digest"],
        "runtime": "runner-east",
        "tested_at": iso(),
        "observed": cloud.identity(p._profile(p.managed_spec("sam"))),
        "source": "operator_supplied_runtime_report",
    }
    if change in {"user", "role"}:
        receipt["observed"][change + "_name"] = "WRONG"
    elif change in {"stale", "future"}:
        receipt["tested_at"] = iso(utcnow() + timedelta(days=-2 if change == "stale" else 2))
    elif change == "fields":
        receipt["observed"]["token_secret"] = "MUST_NOT_CACHE"
    else:
        receipt["source"] = "signed_by_snowflake"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        review_receipt(fleet, "sam", path)
    assert fleet.state("sam")["status"] == "awaiting_verification"
    assert b"MUST_NOT_CACHE" not in fleet.service.store.path.read_bytes()


def test_runtime_bundle_rejects_arbitrary_host_and_secret_fields(setup, tmp_path):
    import json

    from snowbeam.fleet import digest
    from snowbeam.runtime import export_bundle, load_bundle

    fleet, _, p = setup
    apply(p)
    bundle = export_bundle(fleet, "sam")
    path = tmp_path / "bundle.json"
    for key, value in (("host", "collector.example.com"), ("password", "DO_NOT_RESOLVE")):
        bad = copy.deepcopy(bundle)
        bad["profile"]["settings"][key] = value
        bad["bundle_digest"] = digest({k: v for k, v in bad.items() if k != "bundle_digest"})
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load_bundle(path)
    bad = copy.deepcopy(bundle)
    bad["profile"]["settings"]["role"] = "CHANGED"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="changed"):
        load_bundle(path)


def test_policy_contents_drift_under_same_name_is_visible(setup):
    fleet, cloud, p = setup
    apply(p)
    p.verify("sam", "runner-east")
    fleet.refresh("sam")
    row = next(row for row in fleet.identities() if row["id"] == "sam")
    assert row["status"] == "Matches template", row["issues"]
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[ALL]"
    fleet.refresh("sam")
    row = next(row for row in fleet.identities() if row["id"] == "sam")
    assert any("Policy contents changed" in issue for issue in row["issues"])


def test_timed_out_user_creation_retains_recovery_record_without_adopting_user(setup, monkeypatch):
    fleet, cloud, p = setup
    original = cloud.execute

    def uncertain(client, profile, org, account, sql, **kwargs):
        result = original(client, profile, org, account, sql, **kwargs)
        if sql.startswith("CREATE USER"):
            raise SnowError("timeout", "The server result is uncertain.")
        return result

    monkeypatch.setattr("snowbeam.provision.guarded_query", uncertain)
    with pytest.raises(ValueError, match="uncertain"):
        apply(p)
    state = fleet.state("sam")
    assert state["status"] == "recovery_required"
    assert not state.get("created_user")
    assert cloud.users["AGENT_SAM"]["disabled"] is True
    assert "user_creation_requested" in fleet.service.store.operations()[0]["steps"]
    with pytest.raises(ValueError, match="history"):
        p.plan("sam")
    with pytest.raises(ValueError, match="recovery"):
        p.plan("sam", "revoke")


def test_colliding_pat_name_is_never_removed_as_failed_mint_cleanup(setup, monkeypatch):
    fleet, cloud, p = setup
    apply(p)
    p.verify("sam", "runner-east")
    original = cloud.execute

    def collision(client, profile, org, account, sql, **kwargs):
        if "ADD PROGRAMMATIC ACCESS TOKEN" in sql:
            name = sql.split()[7].strip('"')
            cloud.users["AGENT_SAM"]["tokens"][name] = "CONCURRENT_CREDENTIAL"
            raise SnowError("object_exists", "The named Snowflake object already exists.")
        return original(client, profile, org, account, sql, **kwargs)

    monkeypatch.setattr("snowbeam.provision.guarded_query", collision)
    with pytest.raises(ValueError, match="already exists"):
        apply(p, "rotate")
    assert "CONCURRENT_CREDENTIAL" in cloud.users["AGENT_SAM"]["tokens"].values()
    assert not fleet.state("sam").get("pending_name")
    assert "credential_name_collision" in fleet.service.store.operations()[0]["steps"]


def test_runtime_cli_does_not_read_manager_configuration(setup, monkeypatch, tmp_path, capsys):
    from snowbeam.cli import main
    from snowbeam.runtime import export_bundle, write_json

    fleet, cloud, p = setup
    apply(p)
    bundle = tmp_path / "sam.json"
    write_json(bundle, export_bundle(fleet, "sam"))
    forbidden = tmp_path / "manager-config.toml"
    forbidden.write_text('invalid TOML ["MANAGER_SECRET')
    monkeypatch.setattr("snowbeam.runtime.guarded_query", cloud.execute)
    monkeypatch.setattr(
        "snowbeam.runtime.Vault", __import__("snowbeam.provision", fromlist=["Vault"]).Vault
    )
    assert (
        main(
            [
                "--snow-config",
                str(forbidden),
                "--state-dir",
                str(tmp_path / "runtime-cache"),
                "runtime",
                "test",
                str(bundle),
                "--out",
                str(tmp_path / "receipt.json"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "AGENT_SAM" in output
    assert "MANAGER_SECRET" not in output
