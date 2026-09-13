"""Join local intent with observed Snowflake identity and policy metadata."""

from __future__ import annotations

from pathlib import Path

from .config import ConfigError, Profile
from .fleet import FleetConfig, digest
from .security import Inspector, policy_fingerprint, provisioning_policy_issues
from .snowflake import SnowError
from .vault import Vault, VaultError, credential_session, workload_session


class FleetService:
    def __init__(self, service, path: Path | None = None):
        self.service = service
        self.config = FleetConfig(path or service.config.path.with_name("snowbeam.toml"))

    def key(self, identity: str) -> str:
        return f"{self.config.path}::{identity}"

    def state(self, identity: str) -> dict:
        return self.service.store.fleet_state(self.key(identity))

    def save_state(self, identity: str, state: dict) -> None:
        self.service.store.save_fleet_state(self.key(identity), state)

    @staticmethod
    def target(spec: dict) -> dict:
        return {key: spec[key] for key in ("organization", "account", "user")}

    def identities(self, *, client: str | None = None) -> list[dict]:
        document = self.config.read()
        connections = {
            c["name"]: c
            for c in self.service.store.connections()
            if c["config_path"] == str(self.service.config.path)
        }
        tracked = {v["connection"]: k for k, v in document["identities"].items()}
        states = self.service.store.fleet_states()
        accounts = {(a["organization"], a["name"]): a["id"] for a in self.service.store.accounts()}
        rows = []
        for connection in sorted(set(connections) | set(tracked)):
            conn = connections.get(connection, {})
            key = tracked.get(connection)
            spec = self.config.spec(key, document) if key else {}
            state = states.get(self.key(key), {}) if key else {}
            organization = conn.get("organization") or spec.get("organization")
            account = conn.get("account_name") or spec.get("account")
            user = conn.get("user_name") or spec.get("user") or conn.get("settings", {}).get("user")
            account_id = conn.get("account_id")
            if not account_id and organization and account:
                account_id = accounts.get((organization, account))
            evidence = self.service.store.security(account_id, user) if account_id and user else {}
            record = {
                "id": key or f"connection:{connection}",
                "tracked": bool(key),
                "connection": connection,
                "kind": spec.get("kind", "unclassified"),
                "client": spec.get("client", ""),
                "owner": spec.get("owner", ""),
                "purpose": spec.get("purpose", ""),
                "runtime": spec.get("runtime", ""),
                "organization": organization,
                "account_name": account,
                "account_id": account_id,
                "user_name": user,
                "connection_health": conn.get("status", "not_created"),
                "template": spec.get("template"),
                "expected": {
                    k: spec[k]
                    for k in ("role", "auth", "authentication_policy", "network_policy")
                    if k in spec
                },
                "credential": document["credentials"].get(key),
                "lifecycle": state,
                "security": evidence,
            }
            record["issues"] = self.record_issues(record)
            record["status"] = (
                "Needs attention"
                if record["issues"]
                else "Matches template"
                if record["template"]
                else "Inspected"
            )
            rows.append(record)
        references = {}
        for row in rows:
            if row.get("credential"):
                references.setdefault(digest(row["credential"]), []).append(row)
        for row in rows:
            ref = row.get("credential")
            if not ref:
                continue
            shared = [
                other["id"]
                for other in references[digest(ref)]
                if other["id"] != row["id"]
                and other.get("credential") == ref
                and (other["organization"], other["account_name"], other["user_name"])
                != (row["organization"], row["account_name"], row["user_name"])
            ]
            if shared:
                row["issues"].append(
                    "Credential reference is shared with another identity: " + ", ".join(shared)
                )
                row["status"] = "Needs attention"
        return [row for row in rows if client is None or row["client"] == client]

    @staticmethod
    def record_issues(row: dict) -> list[str]:
        issues = []
        if row["connection_health"] != "ok":
            issues.append("Connection is not verified.")
        security = row["security"]
        required = ["user", "grants", "authentication", "network", "tokens"]
        methods = (security.get("authentication", {}).get("data") or {}).get("methods", [])
        required += [
            section
            for section, method in (("keys", "KEYPAIR"), ("workload", "WORKLOAD_IDENTITY"))
            if method in methods or "ALL" in methods
        ]
        if row["expected"].get("role"):
            required.append("role_access")
        for name in required:
            section = security.get(name)
            if not section or section["status"] != "ok":
                issues.append(f"{name.capitalize()} metadata is unavailable.")
            elif section["stale"]:
                issues.append(f"{name.capitalize()} metadata is over 24 hours old.")

        def current(name):
            return security.get(name, {}).get("data") or {}

        user = current("user")
        if str(user.get("DISABLED", "")).lower() == "true":
            issues.append("Snowflake sign-in is disabled.")
        if str(user.get("MINS_TO_BYPASS_NETWORK_POLICY", "0")) not in {"0", "None", "null", ""}:
            issues.append("A network-policy bypass is present; inspect it in Snowflake.")
        expected = row["expected"]
        auth = current("authentication")
        network = current("network")
        if (
            expected.get("authentication_policy")
            and auth
            and auth.get("policy") != expected["authentication_policy"]
        ):
            issues.append("Authentication policy differs from the template.")
        if (
            expected.get("network_policy")
            and network
            and network.get("policy") != expected["network_policy"]
        ):
            issues.append("Network policy differs from the template.")
        if expected.get("auth") and auth and network:
            # Expiry validation happens during provisioning; inventory compares the access boundary.
            issues.extend(provisioning_policy_issues(auth, network, expected["auth"], 1))
        roles = {
            r.get("role") or (r.get("name") if r.get("granted_on") == "ROLE" else None)
            for r in (current("grants") or [])
        }
        if (
            expected.get("role")
            and security.get("grants", {}).get("status") == "ok"
            and expected["role"] not in roles
        ):
            issues.append("Expected role is not directly granted to the identity.")
        if (
            row["kind"] == "agent"
            and row["template"]
            and user
            and user.get("TYPE") != "SERVICE_AGENT"
        ):
            issues.append("User type differs from SERVICE_AGENT.")
        state = row["lifecycle"]
        if (
            state.get("approved_policy_digest")
            and auth
            and network
            and state["approved_policy_digest"] != policy_fingerprint(auth, network)
        ):
            issues.append("Policy contents changed since the approved setup.")
        role_access = current("role_access")
        if (
            state.get("approved_role_digest")
            and role_access
            and digest(role_access.get("grants", [])) != state["approved_role_digest"]
        ):
            issues.append("The workload role's direct grants changed since approval.")
        if "ALL" in methods or set(methods) & {"SAML", "OAUTH", "OIDC"}:
            issues.append("Security integration network overrides need separate inspection.")
        if state.get("workload") and current("workload"):
            from .workload import matches_workload

            if not matches_workload(state["workload"], current("workload")):
                issues.append("Workload identity binding changed since approval.")
        if state.get("status") and state["status"] != "active":
            issues.append("Lifecycle: " + state["status"].replace("_", " ") + ".")
        if state.get("previous_name") or state.get("previous_slot"):
            issues.append("Previous credential still needs retirement.")
        if state.get("credential_name"):
            tokens = current("tokens") or []
            token = next((t for t in tokens if t["name"] == state["credential_name"]), None)
            if security.get("tokens", {}).get("status") == "ok" and (
                not token or token.get("status") != "ACTIVE"
            ):
                issues.append("Managed PAT is missing or not active.")
            elif token and expected.get("role") != token.get("role_restriction"):
                issues.append("Managed PAT role restriction differs from the template.")
        return list(dict.fromkeys(issues))

    def issues(self) -> list[str]:
        # Existing connections become security-tracked when the user labels them.
        return [
            f"{r['id']}: {issue}"
            for r in self.identities()
            if r["tracked"]
            for issue in r["issues"]
        ]

    def refresh(self, identity: str | None = None, *, history: bool = False) -> dict:
        rows = self.identities()
        if identity:
            rows = [r for r in rows if r["id"] == identity or r["connection"] == identity]
            if len(rows) != 1:
                raise ValueError("Select one tracked identity or connection.")
        refreshed, issues = 0, []
        inspected = set()
        for row in rows:
            try:
                spec = self.config.spec(row["id"]) if row["tracked"] else {}
                profile = self.service.config.profile(
                    spec.get("admin_connection") or row["connection"]
                )
                with self.session_for(profile) as actual:
                    observed = self.service.client.identity(actual)
                    if spec.get("template"):
                        if (
                            observed["organization_name"] != spec["organization"]
                            or observed["account_name"] != spec["account"]
                        ):
                            raise SnowError(
                                "wrong_target",
                                "Provisioning connection belongs to a different account.",
                            )
                        user = spec["user"]
                    else:
                        user = observed["user_name"]
                        self.service.store.connected(profile, observed)
                    key = (observed["organization_name"], observed["account_name"], user)
                    if key in inspected:
                        continue
                    inspector = Inspector(self.service.client, actual, *key[:2])
                    sections = inspector.inspect(user, history=history, role=spec.get("role"))
                account_id = self.service.store.ensure_account(observed)
                self.service.store.save_security(account_id, user, sections)
                if sections["tokens"]["status"] == "ok":
                    self.service.store.save_tokens(account_id, user, sections["tokens"]["data"])
                else:
                    self.service.store.tokens_failed(
                        account_id,
                        user,
                        sections["tokens"]["status"],
                        sections["tokens"].get("message", "Unavailable"),
                    )
                for section, value in sections.items():
                    if value["status"] != "ok":
                        issues.append(
                            f"{row['id']}: {section}: {value.get('message', 'Unavailable')}"
                        )
                inspected.add(key)
                refreshed += 1
            except (SnowError, VaultError, ConfigError, OSError) as exc:
                issues.append(f"{row['id']}: {exc}")
                # Preserve last data, but invalidate all sections after a failed refresh.
                if row["account_id"] and row["user_name"]:
                    self.service.store.save_security(
                        row["account_id"],
                        row["user_name"],
                        {
                            s: {
                                "status": "refresh_failed",
                                "message": "Identity refresh failed; previous evidence retained.",
                            }
                            for s in ("user", "grants", "authentication", "network", "tokens")
                        },
                    )
        return {"refreshed": refreshed, "issues": issues}

    def session_for(self, profile: Profile, *, interactive: bool = True):
        from contextlib import nullcontext

        document = self.config.read()
        for key, value in document["identities"].items():
            if value["connection"] == profile.name and value.get("template"):
                from .provision import Provisioner

                spec = self.config.spec(key)
                if self.state(key):
                    Provisioner(self).managed_spec(key)
                expected = Provisioner(self)._profile(spec)
                for field in ("account", "user", "authenticator", "role", "host"):
                    if profile.settings.get(field, "") != expected.settings.get(field, ""):
                        raise ConfigError(
                            "The managed connection changed. Restore its approved account, "
                            "identity, role, authentication, and host before resolving a "
                            "credential."
                        )
                if spec["auth"] == "WIF":
                    return workload_session(expected)
            if value["connection"] == profile.name and (
                reference := document["credentials"].get(key)
            ):
                if not interactive and reference["provider"] == "onepassword":
                    import os

                    if not os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
                        raise SnowError(
                            "sign_in",
                            "Vault-backed connection requires an explicit refresh or an "
                            "authorized 1Password service account.",
                        )
                return credential_session(profile, Vault(reference).read())
        return nullcontext(profile)

    def spec_digest(self, identity: str) -> str:
        return digest(self.config.spec(identity))
