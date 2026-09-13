"""Account-bound, reviewable agent lifecycle operations with an explicit journal."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import os
import secrets
import shutil
import subprocess
import uuid
from contextlib import contextmanager

from .config import Profile
from .fleet import digest, literal, qualified_quote, quote
from .security import (
    AUTH_PROPERTIES,
    Inspector,
    guarded_query,
    policy_fingerprint,
    properties,
    provisioning_policy_issues,
    values,
)
from .snowflake import IDENTITY_SQL, SnowError
from .store import iso
from .vault import Vault, VaultError, credential_session, workload_session
from .workload import matches_workload, validate_workload, workload_sql


@contextmanager
def operation_lock(directory):
    path = directory / "fleet-operation.lock"
    if path.is_symlink():
        raise ValueError("The lifecycle operation lock cannot be a symlink.")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(
                "Another Snowbeam lifecycle operation is running. Retry after it finishes."
            ) from None
        yield


def generate_key() -> tuple[dict, str, str]:
    executable = shutil.which("openssl")
    if not executable:
        raise ValueError("Install OpenSSL before generating a key pair.")
    passphrase = secrets.token_urlsafe(48)
    environment = dict(os.environ, SNOWBEAM_KEY_PASSPHRASE=passphrase)
    try:
        private = subprocess.run(
            [
                executable,
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:3072",
                "-aes-256-cbc",
                "-pass",
                "env:SNOWBEAM_KEY_PASSPHRASE",
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=environment,
            timeout=60,
            check=True,
        ).stdout
        public = subprocess.run(
            [executable, "pkey", "-pubout", "-passin", "env:SNOWBEAM_KEY_PASSPHRASE"],
            input=private,
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
            check=True,
        ).stdout
        encoded = "".join(line for line in public.splitlines() if not line.startswith("---"))
        fingerprint = (
            "SHA256:"
            + base64.b64encode(
                hashlib.sha256(base64.b64decode(encoded, validate=True)).digest()
            ).decode()
        )
        if "BEGIN ENCRYPTED PRIVATE KEY" not in private:
            raise ValueError
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("Encrypted key generation failed. No private key was retained.") from None
    return {"private_key": private, "passphrase": passphrase}, encoded, fingerprint


class Provisioner:
    def __init__(self, fleet):
        self.fleet = fleet
        self.service = fleet.service

    def managed_spec(self, identity: str) -> dict:
        if self.service.demo:
            raise ValueError("Live provisioning is unavailable in demo mode.")
        spec = self.fleet.config.spec(identity)
        if spec["kind"] != "agent" or not spec.get("template"):
            raise ValueError(
                "Lifecycle operations require an agent created from a provisioning template."
            )
        state = self.fleet.state(identity)
        if state and state.get("target") != self.fleet.target(spec):
            raise ValueError(
                "The template now points to a different identity or account. Restore the "
                "original target or create a separate entry."
            )
        if state.get("credential_ref") and state["credential_ref"] != spec.get("credential"):
            raise ValueError(
                "The managed vault reference changed. Restore its recorded reference "
                "before lifecycle operations."
            )
        if state.get("credential_kind") and state["credential_kind"] != spec["auth"]:
            raise ValueError(
                "The authentication method changed. Restore the managed template before "
                "lifecycle operations."
            )
        if state.get("workload") and state["workload"] != validate_workload(spec):
            raise ValueError(
                "The workload binding changed. Restore the managed binding before "
                "lifecycle operations."
            )
        return spec

    def inspector(self, spec, profile):
        return Inspector(self.service.client, profile, spec["organization"], spec["account"])

    def plan(self, identity: str, action: str = "provision") -> dict:
        if action not in {"provision", "rotate", "retire-old", "revoke"}:
            raise ValueError("Unknown lifecycle action.")
        spec = self.managed_spec(identity)
        state = self.fleet.state(identity)
        if spec["auth"] == "WIF" and action in {"rotate", "retire-old"}:
            raise ValueError(
                "The workload identity provider renews short-lived credentials. Use its "
                "lifecycle controls."
            )
        admin = self.service.config.profile(spec["admin_connection"])
        with self.fleet.session_for(admin) as actual:
            inspector = self.inspector(spec, actual)
            observed = self.service.client.identity(actual)
            if (observed["organization_name"], observed["account_name"]) != (
                spec["organization"],
                spec["account"],
            ):
                raise ValueError(
                    "The provisioning connection resolves to a different organization or account."
                )
            if observed["user_name"] == spec["user"]:
                raise ValueError("An agent cannot provision or rotate its own credentials.")
            if action == "provision":
                if state:
                    raise ValueError(
                        "This identity already has lifecycle history. Inspect its status; do not "
                        "recreate it."
                    )
                if any(p.name == spec["connection"] for p in self.service.config.profiles()):
                    raise ValueError(
                        "The agent's connection name already exists. Choose an unused name."
                    )
                existing = inspector.query("SHOW USERS LIKE " + literal(spec["user"]))
                if any(r.get("name") == spec["user"] for r in existing):
                    raise ValueError(
                        "The Snowflake user already exists. Provisioning never adopts or replaces "
                        "an existing user."
                    )
                user = {}
            else:
                if (
                    not state.get("credential_ref")
                    and state.get("credential_kind") != "WIF"
                    and not (action == "revoke" and state.get("created_user"))
                ):
                    raise ValueError(
                        "No completed managed credential exists. Inspect the failed operation and "
                        "recovery instructions."
                    )
                user = inspector.user(spec["user"])
                if user["TYPE"] != "SERVICE_AGENT":
                    raise ValueError(
                        "The managed user is no longer a SERVICE_AGENT; inspect the change before "
                        "proceeding."
                    )
            policies = {}
            role_grants = []
            if action in {"provision", "rotate"}:
                if (
                    state.get("pending_ref")
                    or state.get("previous_ref")
                    or state.get("status") == "revoked"
                ):
                    raise ValueError(
                        "Finish or resolve the previous lifecycle operation before issuing "
                        "another credential."
                    )
                auth_props = properties(
                    inspector.query(
                        "DESCRIBE AUTHENTICATION POLICY "
                        + qualified_quote(spec["authentication_policy"])
                    ),
                    AUTH_PROPERTIES,
                )
                auth = {
                    "policy": spec["authentication_policy"],
                    "properties": auth_props,
                    "methods": values(auth_props.get("AUTHENTICATION_METHODS")),
                }
                network = inspector.network_policy(spec["network_policy"])
                problems = provisioning_policy_issues(
                    auth, network, spec["auth"], spec["days_to_expiry"]
                )
                if problems:
                    raise ValueError(" ".join(problems))
                policies = {"authentication": auth, "network": network}
                grants = inspector.query("SHOW GRANTS TO ROLE " + quote(spec["role"]))
                role_grants = [
                    {
                        k: v
                        for k, v in row.items()
                        if k in {"privilege", "granted_on", "name", "grant_option"}
                    }
                    for row in grants
                ]
                if action == "rotate":
                    self._assert_assignment(inspector, spec)
            if action == "retire-old":
                if not state.get("previous_ref") or not state.get("runtime_verified_at"):
                    raise ValueError(
                        "Verify the replacement credential from its runtime before retiring the "
                        "previous credential."
                    )
            slot = None
            if spec["auth"] == "KEYPAIR" and action in {"provision", "rotate"}:
                for candidate, field in (
                    ("RSA_PUBLIC_KEY", "RSA_PUBLIC_KEY_FP"),
                    ("RSA_PUBLIC_KEY_2", "RSA_PUBLIC_KEY_2_FP"),
                ):
                    if not user.get(field) or str(user[field]).lower() in {"null", "none"}:
                        slot = candidate
                        break
                if not slot:
                    raise ValueError(
                        "Both Snowflake public-key slots are occupied. Snowbeam will not "
                        "overwrite either key."
                    )
            if action in {"retire-old", "revoke"} and spec["auth"] == "KEYPAIR":
                slots = (
                    [state.get("previous_slot")]
                    if action == "retire-old"
                    else [
                        state.get("key_slot"),
                        state.get("previous_slot"),
                        state.get("pending_slot"),
                    ]
                )
                fingerprints = {
                    state.get("key_slot"): state.get("public_key_fp"),
                    state.get("previous_slot"): state.get("previous_fingerprint"),
                    state.get("pending_slot"): state.get("pending_fingerprint"),
                }
                for item in filter(None, slots):
                    if item not in {"RSA_PUBLIC_KEY", "RSA_PUBLIC_KEY_2"}:
                        raise ValueError("Unknown public-key slot in lifecycle state.")
                    if user.get(item + "_FP") and user.get(item + "_FP") != fingerprints.get(item):
                        raise ValueError(
                            "A public-key slot changed outside Snowbeam. Inspect it before "
                            "removing a key."
                        )
            token_name = (
                "SB_" + digest({"spec": spec, "state": state, "action": action})[:24].upper()
            )
            if (
                spec["auth"] == "PAT"
                and action in {"provision", "rotate"}
                and action != "provision"
            ):
                if any(t["name"] == token_name for t in inspector.tokens(spec["user"])):
                    raise ValueError(
                        "The proposed token name already exists. Inspect the previous attempt."
                    )
        plan = {
            "action": action,
            "identity": identity,
            "target": self.fleet.target(spec),
            "admin_connection": spec["admin_connection"],
            "admin_user": observed["user_name"],
            "admin_role": observed.get("role_name"),
            "connection": spec["connection"],
            "authentication": spec["auth"],
            "role": spec["role"],
            "policies": policies,
            "role_grants": role_grants,
            "vault": {
                k: spec[k]
                for k in ("provider", "vault", "vault_account", "vault_server", "vault_user")
                if k in spec
            },
            "runtime": spec.get("runtime", ""),
            "token_name": token_name if spec["auth"] == "PAT" else None,
            "key_slot": slot,
            "days_to_expiry": spec["days_to_expiry"] if spec["auth"] == "PAT" else None,
            "intent_digest": digest(spec),
            "state_digest": digest(state),
            "connection_digest": digest(
                {"path": str(admin.source_path), "settings": admin.settings}
            ),
            "workload": validate_workload(spec) if spec["auth"] == "WIF" else None,
            "host": spec.get("host"),
            "steps": self._steps(action, spec),
        }
        plan["approval"] = digest(plan)
        return plan

    @staticmethod
    def _steps(action, spec):
        if action == "provision" and spec["auth"] == "WIF":
            return [
                "Create a disabled SERVICE_AGENT user",
                "Attach the existing workload role and verified policies",
                "Bind this agent to its existing cloud identity",
                "Write the standard connection profile and enable the user",
                "Verify on the declared runtime; cloud IAM is managed separately",
            ]
        if action == "provision":
            return [
                "Create a disabled SERVICE_AGENT user",
                "Grant the existing workload role and attach the verified policies",
                "Create an individual credential and verify its vault item",
                "Consolidate profiles in connections.toml and add the new connection",
                "Enable the new user; require runtime verification",
            ]
        if action == "rotate":
            return [
                "Create a replacement credential and verify its vault item",
                "Keep the active credential until runtime verification",
                "Retain the previous credential until explicit retirement",
            ]
        if action == "retire-old":
            return [
                "Remove the previously managed Snowflake credential",
                "Retain the vault item and operation history for audit",
            ]
        return [
            "Disable this agent's Snowflake sign-in",
            "Remove only the credentials managed by Snowbeam",
            "Retain the identity, local metadata, and vault items",
        ]

    def _assert_assignment(self, inspector, spec, approved: str | None = None):
        auth = inspector.authentication(spec["user"])
        network = inspector.network(spec["user"])
        grants = inspector.grants(spec["user"])
        roles = {
            r.get("role") or (r.get("name") if r.get("granted_on") == "ROLE" else None)
            for r in grants
        }
        if (
            auth["policy"] != spec["authentication_policy"]
            or network["policy"] != spec["network_policy"]
            or spec["role"] not in roles
        ):
            raise ValueError(
                "The user's effective policies or granted role differ from the template."
            )
        if approved and approved != policy_fingerprint(auth, network):
            raise ValueError("Policy contents changed after approval. Review a fresh plan.")
        problems = provisioning_policy_issues(auth, network, spec["auth"], spec["days_to_expiry"])
        if problems:
            raise ValueError(" ".join(problems))

    def _profile(self, spec):
        settings = {
            "account": spec["organization"] + "-" + spec["account"],
            "user": spec["user"],
            "role": spec["role"],
            "authenticator": {
                "PAT": "PROGRAMMATIC_ACCESS_TOKEN",
                "KEYPAIR": "SNOWFLAKE_JWT",
                "WIF": "WORKLOAD_IDENTITY",
            }[spec["auth"]],
        }
        if spec.get("warehouse"):
            settings["warehouse"] = spec["warehouse"]
        if spec.get("host"):
            settings["host"] = spec["host"]
        if spec["auth"] == "WIF":
            settings["workload_identity_provider"] = spec["workload_provider"]
            if spec.get("workload_token_file"):
                settings["token_file_path"] = spec["workload_token_file"]
        return Profile(
            spec["connection"], self.service.config.path, self.service.config.source, settings
        )

    def apply(self, identity: str, action: str, approval: str) -> dict:
        with operation_lock(self.service.store.directory):
            plan = self.plan(identity, action)
            if not approval or not hmac.compare_digest(approval, plan["approval"]):
                raise ValueError(
                    "The plan changed or was not approved. Review a fresh plan and pass its "
                    "approval digest."
                )
            spec = self.managed_spec(identity)
            state = self.fleet.state(identity)
            operation_id = str(uuid.uuid4())
            steps = []
            target = self.fleet.target(spec)
            key = self.fleet.key(identity)

            def journal(status, message=""):
                self.service.store.save_operation(
                    operation_id, key, action, target, status, steps, message
                )

            journal("running")
            if not state:
                state = {"target": target, "status": "provisioning", "operation_id": operation_id}
                self.fleet.save_state(identity, state)
            admin = self.service.config.profile(spec["admin_connection"])
            approved_policy = (
                policy_fingerprint(plan["policies"]["authentication"], plan["policies"]["network"])
                if plan["policies"]
                else None
            )
            creation_attempted = False
            minted = False
            try:
                with self.fleet.session_for(admin) as actual:
                    inspector = self.inspector(spec, actual)

                    def execute(sql, sensitive=False):
                        return guarded_query(
                            self.service.client,
                            actual,
                            spec["organization"],
                            spec["account"],
                            sql,
                            sensitive=sensitive,
                            user=plan["admin_user"],
                            role=plan["admin_role"],
                        )

                    if action in {"provision", "rotate"} and spec["auth"] != "WIF":
                        vault = Vault(spec)
                        vault.ready()  # Unlock/account checks precede any Snowflake writes.
                        if spec["auth"] == "KEYPAIR" and not shutil.which("openssl"):
                            raise ValueError("Install OpenSSL before generating a key pair.")
                    if action == "provision":
                        creation_attempted = True
                        steps.append("user_creation_requested")
                        journal("running")
                        execute(
                            f"CREATE USER {quote(spec['user'])} TYPE = SERVICE_AGENT DISABLED = "
                            f"TRUE DEFAULT_ROLE = {quote(spec['role'])} DEFAULT_SECONDARY_ROLES = "
                            f"()"
                        )
                        state["created_user"] = True
                        self.fleet.save_state(identity, state)
                        steps.append("user_created_disabled")
                        journal("running")
                        execute(f"GRANT ROLE {quote(spec['role'])} TO USER {quote(spec['user'])}")
                        execute(
                            f"ALTER USER {quote(spec['user'])} SET AUTHENTICATION POLICY "
                            f"{qualified_quote(spec['authentication_policy'])}"
                        )
                        execute(
                            f"ALTER USER {quote(spec['user'])} SET NETWORK_POLICY = "
                            f"{quote(spec['network_policy'])}"
                        )
                        self._assert_assignment(inspector, spec, approved_policy)
                        state["approved_policy_digest"] = approved_policy
                        state["approved_role_digest"] = digest(plan["role_grants"])
                        self.fleet.save_state(identity, state)
                        steps.append("policies_and_role_verified")
                        journal("running")
                    if action in {"provision", "rotate"}:
                        self._assert_assignment(inspector, spec, approved_policy)
                        if spec["auth"] == "WIF":
                            execute(f"ALTER USER {quote(spec['user'])} SET {workload_sql(spec)}")
                            if not matches_workload(spec, inspector.workload(spec["user"])):
                                raise ValueError(
                                    "The registered workload identity does not match the plan."
                                )
                            state.update(
                                credential_kind="WIF",
                                workload=validate_workload(spec),
                                status="awaiting_verification",
                            )
                            self.fleet.save_state(identity, state)
                            steps.append("workload_binding_verified")
                            profile = self._profile(spec)
                            self.service.config.ensure_shared()
                            self.service.config.save(profile.name, profile.settings, create=True)
                            self._assert_assignment(inspector, spec, approved_policy)
                            execute(f"ALTER USER {quote(spec['user'])} SET DISABLED = FALSE")
                            steps.extend(["connection_profile_created", "user_enabled"])
                        else:
                            if spec["auth"] == "PAT":
                                state["pending_name"] = plan["token_name"]
                                self.fleet.save_state(identity, state)
                                minted = True  # A timeout can still mean Snowflake created the PAT.
                                rows = execute(
                                    f"ALTER USER {quote(spec['user'])} ADD PROGRAMMATIC ACCESS "
                                    f"TOKEN {quote(plan['token_name'])} ROLE_RESTRICTION = "
                                    f"{literal(spec['role'])} DAYS_TO_EXPIRY = "
                                    f"{spec['days_to_expiry']}",
                                    sensitive=True,
                                )
                                if (
                                    len(rows) != 1
                                    or rows[0].get("token_name") != plan["token_name"]
                                    or not isinstance(rows[0].get("token_secret"), str)
                                    or not rows[0]["token_secret"]
                                ):
                                    raise VaultError(
                                        "Snowflake did not return the expected newly created "
                                        "credential."
                                    )
                                secret = {"token": rows[0]["token_secret"]}
                                del rows
                                fingerprint, public = None, None
                            else:
                                secret, public, fingerprint = generate_key()
                                state.update(
                                    pending_slot=plan["key_slot"], pending_fingerprint=fingerprint
                                )
                            reference = vault.create(
                                f"Snowbeam · {spec['organization']}-{spec['account']} · "
                                f"{spec['user']} · {operation_id}",
                                secret,
                            )
                            del secret
                            state["pending_ref"] = reference
                            self.fleet.save_state(identity, state)
                            steps.append("vault_write_verified")
                            journal("running")
                            if public:
                                execute(
                                    f"ALTER USER {quote(spec['user'])} SET {plan['key_slot']} = "
                                    f"{literal(public)}"
                                )
                                steps.append("public_key_registered")
                            self._assert_assignment(inspector, spec, approved_policy)
                            state["approved_policy_digest"] = approved_policy
                            state["approved_role_digest"] = digest(plan["role_grants"])
                            state["credential_kind"] = spec["auth"]
                            state["status"] = "awaiting_verification"
                            if action == "provision":
                                profile = self._profile(spec)
                                self.service.config.ensure_shared()
                                self.service.config.save(
                                    profile.name, profile.settings, create=True
                                )
                                self.fleet.config.save("credentials", identity, reference)
                                state["credential_ref"] = reference
                                state["credential_name"] = state.get("pending_name")
                                state["public_key_fp"] = fingerprint
                                state["key_slot"] = plan["key_slot"]
                                steps.append("connection_profile_created")
                                self._assert_assignment(inspector, spec, approved_policy)
                                execute(f"ALTER USER {quote(spec['user'])} SET DISABLED = FALSE")
                                steps.append("user_enabled")
                            self.fleet.save_state(identity, state)
                    elif action == "retire-old":
                        if state.get("previous_name"):
                            execute(
                                f"ALTER USER {quote(spec['user'])} REMOVE PROGRAMMATIC ACCESS "
                                f"TOKEN {quote(state['previous_name'])}"
                            )
                        elif state.get("previous_slot"):
                            execute(
                                f"ALTER USER {quote(spec['user'])} UNSET {state['previous_slot']}"
                            )
                        for field in (
                            "previous_name",
                            "previous_slot",
                            "previous_ref",
                            "previous_fingerprint",
                        ):
                            state.pop(field, None)
                        steps.append("previous_credential_removed")
                        self.fleet.save_state(identity, state)
                    else:
                        execute(f"ALTER USER {quote(spec['user'])} SET DISABLED = TRUE")
                        state["status"] = "revoked"
                        self.fleet.save_state(identity, state)
                        steps.append("sign_in_disabled")
                        journal("running")
                        if spec["auth"] == "PAT":
                            existing = {t["name"] for t in inspector.tokens(spec["user"])}
                            for token in {
                                state.get("credential_name"),
                                state.get("pending_name"),
                                state.get("previous_name"),
                            } - {None}:
                                if token in existing:
                                    execute(
                                        f"ALTER USER {quote(spec['user'])} REMOVE PROGRAMMATIC "
                                        f"ACCESS TOKEN {quote(token)}"
                                    )
                        elif spec["auth"] == "WIF":
                            execute(f"ALTER USER {quote(spec['user'])} UNSET WORKLOAD_IDENTITY")
                        else:
                            for slot in {
                                state.get("key_slot"),
                                state.get("pending_slot"),
                                state.get("previous_slot"),
                            } - {None}:
                                execute(f"ALTER USER {quote(spec['user'])} UNSET {slot}")
                        steps.append("managed_credentials_removed")
                    state["operation_id"] = operation_id
                    self.fleet.save_state(identity, state)
                    journal("succeeded")
            except (SnowError, VaultError, ValueError, OSError) as exc:
                cleanup_failed = False
                if (
                    minted
                    and isinstance(exc, SnowError)
                    and exc.code in {"exists", "object_exists"}
                ):
                    # A competing creator owns this name; never clean up its credential.
                    minted = False
                    state.pop("pending_name", None)
                    steps.append("credential_name_collision")
                if minted and not state.get("pending_ref"):
                    try:
                        with self.fleet.session_for(admin) as actual:
                            guarded_query(
                                self.service.client,
                                actual,
                                spec["organization"],
                                spec["account"],
                                f"ALTER USER {quote(spec['user'])} REMOVE PROGRAMMATIC ACCESS "
                                f"TOKEN {quote(plan['token_name'])}",
                                user=plan["admin_user"],
                                role=plan["admin_role"],
                            )
                        state.pop("pending_name", None)
                        steps.append("unsaved_pat_removed")
                    except (SnowError, VaultError, OSError, ValueError):
                        cleanup_failed = True
                        steps.append("pat_cleanup_unconfirmed")
                state["status"] = (
                    "recovery_required"
                    if cleanup_failed or creation_attempted or state.get("pending_ref")
                    else "failed"
                )
                state["message"] = str(exc)
                state["operation_id"] = operation_id
                if action == "provision" and not creation_attempted and not minted:
                    state = {}
                self.fleet.save_state(identity, state)
                journal("recovery_required" if cleanup_failed else "failed", str(exc))
                raise ValueError(
                    f"Operation stopped: {exc} Inspect identities show {identity} and "
                    f"operations; completed steps are recorded."
                ) from None
            self.service.import_profiles()
            return {
                "operation": operation_id,
                "identity": identity,
                "action": action,
                "status": state["status"],
                "steps": steps,
                "next": "Run identities verify from the declared runtime."
                if action in {"provision", "rotate"}
                else "Refresh the identity to inspect current Snowflake state.",
            }

    def verify(self, identity: str, runtime: str) -> dict:
        with operation_lock(self.service.store.directory):
            spec = self.managed_spec(identity)
            if not runtime or runtime != spec.get("runtime"):
                raise ValueError(
                    "Supply the declared runtime label. Run this verification on that runtime."
                )
            state = self.fleet.state(identity)
            if state.get("status") == "revoked":
                raise ValueError("This managed identity was revoked.")
            reference = state.get("pending_ref") or state.get("credential_ref")
            if not reference and spec["auth"] != "WIF":
                raise ValueError("No saved credential is available for verification.")
            profile = self._profile(spec)
            sql = (
                IDENTITY_SQL
                + ", CURRENT_IP_ADDRESS() AS CLIENT_IP, SYS_CONTEXT('SNOWFLAKE$SESSION', "
                "'IP_ADDRESS_V6') AS CLIENT_IPV6"
            )
            session = (
                workload_session(profile)
                if spec["auth"] == "WIF"
                else credential_session(profile, Vault(reference).read())
            )
            with session as actual:
                rows = guarded_query(
                    self.service.client,
                    actual,
                    spec["organization"],
                    spec["account"],
                    sql,
                    user=spec["user"],
                )
            observed = self.service.client._identity(rows)
            if observed.get("role_name") != spec["role"]:
                raise ValueError("Runtime authentication succeeded with an unexpected role.")
            return self.record_verification(identity, observed, runtime)

    def record_verification(
        self,
        identity: str,
        observed: dict,
        runtime: str,
        *,
        source: str = "local_snowflake_query",
        verified_at: str | None = None,
    ) -> dict:
        spec = self.managed_spec(identity)
        state = self.fleet.state(identity)
        profile = self._profile(spec)
        reference = state.get("pending_ref") or state.get("credential_ref")
        if state.get("pending_ref"):
            if state.get("credential_ref") and state["credential_ref"] != reference:
                state.update(
                    previous_ref=state["credential_ref"],
                    previous_name=state.get("credential_name"),
                    previous_slot=state.get("key_slot"),
                    previous_fingerprint=state.get("public_key_fp"),
                )
            state.update(
                credential_ref=reference,
                credential_name=state.get("pending_name"),
                key_slot=state.get("pending_slot") or state.get("key_slot"),
                public_key_fp=state.get("pending_fingerprint") or state.get("public_key_fp"),
            )
            self.fleet.config.save("credentials", identity, reference)
            for key in ("pending_name", "pending_ref", "pending_slot", "pending_fingerprint"):
                state.pop(key, None)
        state.update(
            status="active",
            runtime_verified_at=verified_at or iso(),
            verification_source=source,
            runtime_ip=observed.get("client_ipv6") or observed.get("client_ip"),
            runtime_role=observed["role_name"],
        )
        state.pop("message", None)
        self.fleet.save_state(identity, state)
        self.service.import_profiles()
        account_id = self.service.store.connected(
            self.service.config.profile(profile.name), observed
        )
        return {
            "identity": identity,
            "runtime": runtime,
            "runtime_label_source": "operator supplied",
            "account_id": account_id,
            "user": spec["user"],
            "role": observed["role_name"],
            "ip": state["runtime_ip"],
            "verified_at": state["runtime_verified_at"],
            "previous_credential_needs_retirement": bool(state.get("previous_ref")),
        }
