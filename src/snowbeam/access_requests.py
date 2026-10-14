"""YAML access requests become reviewed plans; mutations require an explicit approval digest."""

from __future__ import annotations

import hmac
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .admin_roles import accountadmin_warning, minimum_admin_role
from .airlock import detect_airlock_context
from .capabilities import normalize_capabilities, run_capability_checks
from .fleet import digest, identifier, quote
from .provision import Provisioner
from .security import Inspector, guarded_query
from .starters import account_key, expand_starter
from .store import iso

REQUIRED_TOP = {
    "version",
    "requester",
    "purpose",
    "owner",
    "runtime",
    "lifetime_end",
    "auth",
    "accounts",
    "capabilities",
    "identity",
    "user",
}


def _parse_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError("lifetime_end must be an ISO date (YYYY-MM-DD).") from None
    if parsed < date.today():
        raise ValueError("lifetime_end must be today or in the future.")
    return parsed.isoformat()


def load_request(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raise ValueError("Cannot read a valid YAML access request.") from None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Access requests must declare version: 1.")
    missing = REQUIRED_TOP - set(data)
    if missing:
        raise ValueError("Missing access request fields: " + ", ".join(sorted(missing)))
    if data.get("auth", "").upper() not in {"KEYPAIR", "KEY_PAIR"}:
        raise ValueError("v1 access requests support KEYPAIR authentication only.")
    if not data.get("role") and not data.get("starter"):
        raise ValueError("Name an existing role or choose a role starter.")
    accounts = data["accounts"]
    if not isinstance(accounts, list) or not accounts or len(accounts) != 1:
        raise ValueError("v1 access requests support exactly one account entry.")
    account = accounts[0]
    if (
        not isinstance(account, dict)
        or not account.get("organization")
        or not account.get("account")
    ):
        raise ValueError("Each account needs organization and account.")
    data["lifetime_end"] = _parse_date(data["lifetime_end"])
    data["capabilities"] = normalize_capabilities(list(data.get("capabilities") or []))
    data["must_not"] = list(data.get("must_not") or [])
    identifier(data["user"])
    from .fleet import name as fleet_name

    fleet_name(str(data["identity"]))
    return data


def resolve_admin_connection(fleet, organization: str, account: str) -> str:
    doc = fleet.config.read()
    key = account_key(organization, account)
    if key in doc.get("account_admins", {}):
        return doc["account_admins"][key]
    raise ValueError(
        f"No admin connection is marked for {key}. Use "
        f"'snowbeam accounts mark-admin CONNECTION --organization {organization} "
        f"--account {account}'."
    )


def _grant_signature(row: dict) -> tuple:
    return (
        str(row.get("privilege", "")).upper(),
        str(row.get("granted_on", "")).upper(),
        str(row.get("name", "")).upper(),
    )


def grants_beyond_request(role_grants: list[dict], capabilities: list[str]) -> list[dict]:
    if not capabilities:
        return []
    allowed_objects = set()
    for cap in capabilities:
        if cap in {"show_grants", "show_users", "show_roles"}:
            allowed_objects.add(("MONITOR", "ACCOUNT"))
            allowed_objects.add(("IMPORTED PRIVILEGES", "DATABASE"))
        if cap in {"show_tables", "select_tables"}:
            allowed_objects.add(("USAGE", "DATABASE"))
            allowed_objects.add(("USAGE", "SCHEMA"))
            allowed_objects.add(("SELECT", "TABLE"))
    extras = []
    for row in role_grants:
        sig = _grant_signature(row)
        if sig[0] in {"USAGE", "MONITOR", "SELECT", "IMPORTED PRIVILEGES"}:
            if (sig[0], sig[1]) not in allowed_objects and sig[1] not in {
                "ROLE",
                "WAREHOUSE",
            }:
                extras.append(row)
        elif sig[0] not in {"", "OWNERSHIP"}:
            extras.append(row)
    return extras


class AccessRequestPlanner:
    def __init__(self, fleet):
        self.fleet = fleet
        self.service = fleet.service

    def plan_from_request(self, request: dict, *, template_name: str) -> dict:
        account = request["accounts"][0]
        organization = identifier(account["organization"])
        account_name = identifier(account["account"])
        role = identifier(request["role"]) if request.get("role") else None
        admin_connection = resolve_admin_connection(self.fleet, organization, account_name)
        admin = self.service.config.profile(admin_connection)
        doc = self.fleet.config.read()
        if template_name not in doc["templates"]:
            raise ValueError("Choose an existing provisioning template for this request.")
        template = doc["templates"][template_name]
        if template["organization"] != organization or template["account"] != account_name:
            raise ValueError("The template belongs to a different organization or account.")
        if template["auth"] != "KEYPAIR":
            raise ValueError("v1 access requests require a KEYPAIR template.")
        starter_sql: list[dict] = []
        role_exists = False
        role_grants: list[dict] = []
        with self.fleet.session_for(admin) as actual:
            observed = self.service.client.identity(actual)
            inspector = Inspector(self.service.client, actual, organization, account_name)
            if request.get("starter"):
                role = identifier(request["role"])
                params = dict(request.get("starter_params") or {})
                for statement in expand_starter(
                    request["starter"],
                    role,
                    preferences=doc.get("preferences", {}),
                    user_starters=doc.get("starters", {}),
                    params=params,
                ):
                    starter_sql.append(
                        {
                            "sql": statement,
                            "minimum_admin_role": minimum_admin_role(statement),
                        }
                    )
                role_exists = False
            elif role:
                rows = inspector.query("SHOW ROLES LIKE " + quote(role))
                role_exists = any(r.get("name") == role for r in rows)
                if not role_exists:
                    raise ValueError(
                        f"Role {role} does not exist. Choose a starter to create it first."
                    )
                grants = inspector.query("SHOW GRANTS TO ROLE " + quote(role))
                role_grants = [
                    {
                        k: v
                        for k, v in row.items()
                        if k in {"privilege", "granted_on", "name", "grant_option"}
                    }
                    for row in grants
                ]
            else:
                raise ValueError("Name a role or provide a starter.")
            airlock = detect_airlock_context(role_grants, role or "")
        beyond = grants_beyond_request(role_grants, request["capabilities"])
        identity = request["identity"]
        create_identity = identity not in doc["identities"]
        lifecycle_action = "provision"
        if not create_identity:
            state = self.fleet.state(identity)
            if state.get("credential_ref"):
                lifecycle_action = "rotate"
        labels = {
            "client": request.get("client", ""),
            "owner": request["owner"],
            "purpose": request["purpose"],
            "runtime": request["runtime"],
            "lifetime_end": request["lifetime_end"],
            "requester": request["requester"],
        }
        intent = self.fleet.config.intent_spec(identity, template_name, request["user"], labels)
        intent["admin_connection"] = admin_connection
        intent["role"] = role or template["role"]
        lifecycle_plan = Provisioner(self.fleet).plan_for_spec(
            intent, lifecycle_action, self.fleet.state(identity) if not create_identity else {}
        )
        admin_warning = accountadmin_warning(observed.get("role_name"))
        plan: dict[str, Any] = {
            "kind": "access_request",
            "version": 1,
            "principle": (
                "Governance changes require a human or directed copilot. This plan gathers "
                "evidence only; nothing applies without the approval digest."
            ),
            "requester": request["requester"],
            "purpose": request["purpose"],
            "owner": request["owner"],
            "runtime": request["runtime"],
            "client": request.get("client", ""),
            "identity": identity,
            "user": request["user"],
            "role": role,
            "role_exists": role_exists,
            "role_grants": role_grants,
            "grants_beyond_request": beyond,
            "capabilities": request["capabilities"],
            "must_not": request["must_not"],
            "lifetime_end": request["lifetime_end"],
            "account": {"organization": organization, "account": account_name},
            "admin_connection": admin_connection,
            "admin_user": observed["user_name"],
            "admin_role": observed.get("role_name"),
            "admin_role_warning": admin_warning,
            "starter_sql": starter_sql,
            "airlock": airlock,
            "create_identity": create_identity,
            "template": template_name,
            "lifecycle_action": lifecycle_action,
            "lifecycle_plan": lifecycle_plan,
            "steps": [
                "Review role grants and flagged extras",
                "Apply additive starter SQL only after approval",
                "Run managed identity lifecycle with the same approval",
                "Run capability verification evidence on the runtime",
            ],
        }
        plan["approval"] = digest(plan)
        return plan

    def apply(self, plan: dict, approval: str) -> dict:
        if not approval or not hmac.compare_digest(approval, plan["approval"]):
            raise ValueError(
                "The plan changed or was not approved. Review a fresh plan and pass its "
                "approval digest."
            )
        account = plan["account"]
        admin = self.service.config.profile(plan["admin_connection"])
        steps: list[str] = []
        with self.fleet.session_for(admin) as actual:
            for item in plan.get("starter_sql", []):
                guarded_query(
                    self.service.client,
                    actual,
                    account["organization"],
                    account["account"],
                    item["sql"],
                    user=plan["admin_user"],
                    role=plan["admin_role"],
                )
                steps.append("starter_sql_applied")
        identity = plan["identity"]
        if plan.get("create_identity"):
            self.fleet.config.add_agent(
                identity,
                plan["template"],
                plan["user"],
                identity,
                client=plan.get("client", ""),
                owner=plan["owner"],
                purpose=plan["purpose"],
                runtime=plan["runtime"],
                lifetime_end=plan["lifetime_end"],
                requester=plan["requester"],
            )
            steps.append("identity_recorded")
        lifecycle_plan = Provisioner(self.fleet).plan(identity, plan["lifecycle_action"])
        provision = Provisioner(self.fleet).apply(
            identity, plan["lifecycle_action"], lifecycle_plan["approval"]
        )
        state = self.fleet.state(identity)
        state["lifetime_end"] = plan["lifetime_end"]
        state["requested_capabilities"] = plan["capabilities"]
        self.fleet.save_state(identity, state)
        steps.extend(provision.get("steps", []))
        result = {
            "identity": identity,
            "status": provision["status"],
            "steps": steps,
            "next": "Run snowbeam identities verify-capabilities after runtime sign-in.",
        }
        if provision.get("credential_relay"):
            result["credential_relay"] = provision["credential_relay"]
        return result

    def verify_capabilities(
        self,
        identity: str,
        *,
        token_file=None,
        private_key_file=None,
        passphrase: str | None = None,
    ) -> dict:
        spec = Provisioner(self.fleet).managed_spec(identity)
        state = self.fleet.state(identity)
        capabilities = state.get("requested_capabilities") or []
        if not capabilities:
            raise ValueError("No requested capabilities were recorded for this identity.")
        profile = Provisioner(self.fleet)._profile(spec)
        reference = state.get("pending_ref") or state.get("credential_ref")
        from pathlib import Path

        from .credential_relay import vault_integration_enabled, verification_session

        token_path = Path(token_file) if token_file else None
        key_path = Path(private_key_file) if private_key_file else None
        use_vault = (
            vault_integration_enabled(self.fleet.config.read())
            and bool(reference)
            and not (token_path or key_path)
        )
        with verification_session(
            profile,
            spec,
            token_file=token_path,
            private_key_file=key_path,
            passphrase=passphrase,
            reference=reference,
            vault_integration=use_vault,
        ) as actual:
            checks = run_capability_checks(
                self.service.client,
                actual,
                spec["organization"],
                spec["account"],
                capabilities,
            )
        state["capability_verification"] = {"checked_at": iso(), "checks": checks}
        self.fleet.save_state(identity, state)
        return {
            "identity": identity,
            "checked_at": state["capability_verification"]["checked_at"],
            "checks": checks,
            "notice": (
                "Evidence only. Failed checks include suggested grants for a new human-reviewed "
                "plan. Snowbeam never applies fixes automatically."
            ),
        }


def import_plan(fleet, path: Path, *, template: str) -> dict:
    return AccessRequestPlanner(fleet).plan_from_request(load_request(path), template_name=template)


def expiring_identities(fleet, *, within_days: int = 30) -> list[dict]:
    cutoff = date.today() + timedelta(days=within_days)
    rows = []
    for row in fleet.identities():
        end = (row.get("lifecycle") or {}).get("lifetime_end")
        if not end:
            end = row.get("lifetime_end")
        if not end:
            continue
        try:
            when = date.fromisoformat(end)
        except ValueError:
            continue
        if when <= cutoff:
            rows.append(
                {
                    "identity": row["id"],
                    "user": row["user_name"],
                    "organization": row["organization"],
                    "account": row["account_name"],
                    "lifetime_end": end,
                    "owner": row.get("owner"),
                    "runtime": row.get("runtime"),
                    "status": row.get("status"),
                }
            )
    return sorted(rows, key=lambda r: r["lifetime_end"])
