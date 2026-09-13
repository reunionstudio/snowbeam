"""Explicit, privilege-dependent security metadata. No inferred green checks."""

from __future__ import annotations

import ipaddress
import json
import re

from .fleet import digest, literal, quote
from .snowflake import SnowError, parse_rows, timestamp

USER_PROPERTIES = {
    "NAME",
    "TYPE",
    "DISABLED",
    "DEFAULT_ROLE",
    "DEFAULT_WAREHOUSE",
    "RSA_PUBLIC_KEY_FP",
    "RSA_PUBLIC_KEY_2_FP",
    "MINS_TO_BYPASS_NETWORK_POLICY",
    "MINS_TO_BYPASS_NETWORK_POLICY_REQUIREMENT",
}
AUTH_PROPERTIES = {
    "NAME",
    "AUTHENTICATION_METHODS",
    "CLIENT_TYPES",
    "SECURITY_INTEGRATIONS",
    "PAT_POLICY",
    "WORKLOAD_IDENTITY_POLICY",
}
NETWORK_PROPERTIES = {
    "ALLOWED_IP_LIST",
    "BLOCKED_IP_LIST",
    "ALLOWED_NETWORK_RULE_LIST",
    "BLOCKED_NETWORK_RULE_LIST",
}
GRANT_FIELDS = {
    "role",
    "privilege",
    "granted_on",
    "name",
    "granted_to",
    "grantee_name",
    "grant_option",
}
TOKEN_FIELDS = {"name", "user_name", "status", "expires_at", "created_on", "role_restriction"}


def properties(rows: list[dict], allowed: set[str]) -> dict:
    result = {}
    for row in rows:
        key = str(row.get("property") or row.get("name") or "").upper()
        if key in allowed:
            value = row.get("property_value", row.get("value"))
            if value is None or isinstance(value, (str, bool, int, float)):
                result[key] = value
    return result


def values(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    text = str(value).strip()
    try:
        decoded = json.loads(text)
        if isinstance(decoded, list):
            return [str(x) for x in decoded]
    except ValueError:
        pass
    # DESCRIBE uses forms such as [KEYPAIR, PROGRAMMATIC_ACCESS_TOKEN].
    return [part.strip().strip("'\"") for part in text.strip("[]").split(",") if part.strip()]


def pat_setting(value: object, key: str) -> str | None:
    # Policy properties have used both JSON and Snowflake's {KEY=VALUE} display form.
    match = re.search(
        r'(?:^|[,{\s])"?' + re.escape(key) + r'"?\s*[:=]\s*"?([A-Za-z0-9_]+)', str(value), re.I
    )
    return match[1].upper() if match else None


def account_guard(
    organization: str, account: str, user: str | None = None, role: str | None = None
) -> str:
    condition = (
        f"COALESCE(CURRENT_ORGANIZATION_NAME(), '') <> {literal(organization)} OR "
        f"COALESCE(CURRENT_ACCOUNT_NAME(), '') <> {literal(account)}"
    )
    if user:
        condition += f" OR COALESCE(CURRENT_USER(), '') <> {literal(user)}"
    if role:
        condition += f" OR COALESCE(CURRENT_ROLE(), '') <> {literal(role)}"
    block = (
        f"DECLARE wrong_target EXCEPTION (-20001, 'Snowbeam account or identity "
        f"mismatch'); BEGIN IF ({condition}) THEN RAISE wrong_target; END IF; END;"
    )
    return "EXECUTE IMMEDIATE " + literal(block)


def guarded_query(
    client,
    profile,
    organization: str,
    account: str,
    sql: str,
    *,
    interactive: bool = True,
    user: str | None = None,
    role: str | None = None,
    sensitive: bool = False,
) -> list[dict]:
    """Guard and operation share one CLI session; CLI stops on a failed statement."""
    output = (client.secure_output if sensitive else client._output)(
        profile,
        account_guard(organization, account, user, role) + "; " + sql,
        interactive=interactive,
    )
    try:
        sets = json.loads(output)
    except (TypeError, ValueError):
        raise SnowError("output", "Snowflake returned invalid guarded-command output.") from None
    if not isinstance(sets, list) or len(sets) != 2 or not all(isinstance(s, list) for s in sets):
        raise SnowError("output", "Snowflake did not return both guarded-command results.")
    return parse_rows(json.dumps(sets[1]))


def policy_fingerprint(authentication: dict, network: dict) -> str:
    return digest(
        {
            "authentication": authentication,
            "network": {k: network.get(k) for k in ("policy", "properties", "rules")},
        }
    )


class Inspector:
    def __init__(
        self, client, profile, organization: str, account: str, *, interactive: bool = True
    ):
        self.client = client
        self.profile = profile
        self.organization = organization
        self.account = account
        self.interactive = interactive

    def query(self, sql: str) -> list[dict]:
        return guarded_query(
            self.client,
            self.profile,
            self.organization,
            self.account,
            sql,
            interactive=self.interactive,
        )

    def user(self, user: str) -> dict:
        result = properties(self.query(f"DESCRIBE USER {quote(user)}"), USER_PROPERTIES)
        if result.get("NAME") != user or not result.get("TYPE") or "DISABLED" not in result:
            raise SnowError(
                "output", "User metadata is incomplete or belongs to a different identity."
            )
        return result

    def grants(self, user: str) -> list[dict]:
        rows = self.query(f"SHOW GRANTS TO USER {quote(user)}")
        if any(row.get("grantee_name") != user for row in rows):
            raise SnowError("output", "Grants do not match the requested user.")
        return [{key: value for key, value in row.items() if key in GRANT_FIELDS} for row in rows]

    def authentication(self, user: str) -> dict:
        rows = self.query(f"SHOW AUTHENTICATION POLICIES ON USER {quote(user)}")
        if len(rows) != 1 or not rows[0].get("name"):
            raise SnowError("unknown", "Effective authentication policy could not be established.")
        row = rows[0]
        parts = [str(row[k]) for k in ("database_name", "schema_name", "name") if row.get(k)]
        identifier = ".".join(quote(p) for p in parts)
        props = properties(
            self.query(f"DESCRIBE AUTHENTICATION POLICY {identifier}"), AUTH_PROPERTIES
        )
        if not props.get("AUTHENTICATION_METHODS"):
            raise SnowError("unknown", "Allowed authentication methods are unavailable.")
        return {
            "policy": ".".join(parts),
            "properties": props,
            "methods": values(props["AUTHENTICATION_METHODS"]),
        }

    def network_policy(self, policy: str) -> dict:
        props = properties(
            self.query(f"DESCRIBE NETWORK POLICY {quote(policy)}"), NETWORK_PROPERTIES
        )
        if not props:
            raise SnowError("unknown", "Network policy details are unavailable.")
        rules = []
        for field in ("ALLOWED_NETWORK_RULE_LIST", "BLOCKED_NETWORK_RULE_LIST"):
            for rule in values(props.get(field)):
                # Fully qualified ordinary rule names are unambiguous. Unusual names
                # remain an explicit gap, never interpreted as executable fragments.
                if not re.fullmatch(r"[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*", rule):
                    raise SnowError(
                        "unknown",
                        "A network rule needs manual inspection because its qualified name is "
                        "ambiguous.",
                    )
                rows = self.query(
                    "DESCRIBE NETWORK RULE " + ".".join(quote(p) for p in rule.split("."))
                )
                if len(rows) != 1 or not rows[0].get("type") or "value_list" not in rows[0]:
                    raise SnowError("unknown", "Network rule details are incomplete.")
                row = rows[0]
                rules.append(
                    {
                        "name": rule,
                        "action": "allow" if field.startswith("ALLOWED") else "block",
                        "type": str(row["type"]),
                        "mode": str(row.get("mode", "")),
                        "values": values(row["value_list"]),
                    }
                )
        return {"policy": policy, "properties": props, "rules": rules}

    def network(self, user: str) -> dict:
        rows = self.query(f"SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN USER {quote(user)}")
        rows = [r for r in rows if str(r.get("key", "")).upper() == "NETWORK_POLICY"]
        if len(rows) != 1:
            raise SnowError("unknown", "Network policy assignment is unavailable.")
        row = rows[0]
        if not row.get("value"):
            return {
                "policy": None,
                "level": str(row.get("level", "")),
                "properties": {},
                "rules": [],
            }
        return {
            **self.network_policy(str(row["value"])),
            "level": str(row.get("level", "")),
            "scope": "account/user; security integration overrides require separate inspection",
        }

    def tokens(self, user: str) -> list[dict]:
        rows = self.query(f"SHOW USER PROGRAMMATIC ACCESS TOKENS FOR USER {quote(user)}")
        if any(row.get("user_name") != user or not row.get("name") for row in rows):
            raise SnowError("output", "Token metadata does not match the requested user.")
        return [{key: value for key, value in row.items() if key in TOKEN_FIELDS} for row in rows]

    def workload(self, user: str) -> list[dict]:
        rows = self.query(
            f"SHOW USER WORKLOAD IDENTITY AUTHENTICATION METHODS FOR USER {quote(user)}"
        )
        result = []
        for row in rows:
            if row.get("name") != user or not row.get("type"):
                raise SnowError("output", "Workload metadata does not match this user.")
            info = row.get("additional_info") or {}
            if isinstance(info, str):
                try:
                    info = json.loads(info)
                except ValueError:
                    raise SnowError(
                        "unknown", "Workload identity details could not be parsed."
                    ) from None
            if not isinstance(info, dict):
                raise SnowError("unknown", "Workload identity details are unavailable.")
            result.append(
                {
                    "name": user,
                    "type": row["type"],
                    "last_used": timestamp(row.get("last_used")),
                    "additional_info": {
                        k: v
                        for k, v in info.items()
                        if k
                        in {
                            "awsPartition",
                            "awsAccount",
                            "type",
                            "iamRole",
                            "issuer",
                            "subject",
                            "audienceList",
                        }
                    },
                }
            )
        return result

    def keys(self, user: str) -> list[dict]:
        rows = self.query(f"SHOW USER KEY PAIRS FOR USER {quote(user)}")
        if any(r.get("user_name") != user or not r.get("name") for r in rows):
            raise SnowError("output", "Named key metadata does not match this user.")
        return [
            {
                k: v
                for k, v in r.items()
                if k
                in {
                    "name",
                    "user_name",
                    "fingerprint",
                    "role_scope",
                    "status",
                    "created_on",
                    "last_used_on",
                    "expires_at",
                }
            }
            for r in rows
        ]

    def login(self, user: str) -> dict:
        rows = self.query(
            "SELECT EVENT_TIMESTAMP, CLIENT_IP, FIRST_AUTHENTICATION_FACTOR, "
            "IS_SUCCESS FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY WHERE USER_NAME = "
            + literal(user)
            + " ORDER BY EVENT_TIMESTAMP DESC LIMIT 1"
        )
        if not rows:
            return {
                "event": None,
                "note": "No visible login; account history may lag by up to two hours.",
            }
        row = rows[0]
        return {
            "at": timestamp(row.get("event_timestamp")),
            "ip": row.get("client_ip"),
            "method": row.get("first_authentication_factor"),
            "success": row.get("is_success"),
            "scope": "latest login for user, not proof of a specific token or runtime",
        }

    def inspect(self, user: str, *, history: bool = False, role: str | None = None) -> dict:
        sections = {}
        for section in (
            "user",
            "grants",
            "authentication",
            "network",
            "tokens",
            *(["login"] if history else []),
        ):
            try:
                sections[section] = {"status": "ok", "data": getattr(self, section)(user)}
            except SnowError as exc:
                sections[section] = {"status": exc.code, "message": str(exc)}
        if role:
            try:
                rows = self.query("SHOW GRANTS TO ROLE " + quote(role))
                sections["role_access"] = {
                    "status": "ok",
                    "data": {
                        "role": role,
                        "grants": [
                            {
                                k: v
                                for k, v in row.items()
                                if k in {"privilege", "granted_on", "name", "grant_option"}
                            }
                            for row in rows
                        ],
                    },
                }
            except SnowError as exc:
                sections["role_access"] = {"status": exc.code, "message": str(exc)}
        methods = sections.get("authentication", {}).get("data", {}).get("methods", [])
        for section, method in (("keys", "KEYPAIR"), ("workload", "WORKLOAD_IDENTITY")):
            if method in methods or "ALL" in methods:
                try:
                    sections[section] = {"status": "ok", "data": getattr(self, section)(user)}
                except SnowError as exc:
                    sections[section] = {"status": exc.code, "message": str(exc)}
        return sections


def provisioning_policy_issues(auth: dict, network: dict, method: str, days: int) -> list[str]:
    """Fail closed on unknown formats and policy settings; never weaken policy."""
    issues = []
    expected = {
        "PAT": "PROGRAMMATIC_ACCESS_TOKEN",
        "KEYPAIR": "KEYPAIR",
        "WIF": "WORKLOAD_IDENTITY",
    }[method]
    if set(auth.get("methods", [])) != {expected}:
        issues.append(f"The authentication policy must allow only {expected}.")
    if method == "PAT":
        pat = auth.get("properties", {}).get("PAT_POLICY", "")
        if pat_setting(pat, "NETWORK_POLICY_EVALUATION") != "ENFORCED_REQUIRED":
            issues.append("PAT policy must explicitly require and enforce the network policy.")
        maximum = pat_setting(pat, "MAX_EXPIRY_IN_DAYS")
        if not maximum or not maximum.isdigit() or days > int(maximum):
            issues.append(
                "Requested PAT expiry exceeds the verified policy maximum or the maximum "
                "is unknown."
            )
    props = network.get("properties", {})
    allowed = values(props.get("ALLOWED_IP_LIST"))
    rules = [r for r in network.get("rules", []) if r["action"] == "allow"]
    if not allowed and not rules:
        issues.append("Network policy must contain an explicit allow rule.")
    if any(r.get("mode") != "INGRESS" for r in rules):
        issues.append("Allowed network rules must apply to ingress traffic.")
    for value in allowed + [v for r in rules if r["type"] in {"IPV4", "IPV6"} for v in r["values"]]:
        try:
            if ipaddress.ip_network(value, strict=False).prefixlen == 0:
                issues.append("Provisioning templates cannot allow the entire internet.")
        except ValueError:
            issues.append("An allowed network address could not be verified.")
    return list(dict.fromkeys(issues))


def sanitize_section(section: str, data):
    """An allowlist at the persistence boundary, including nested metadata."""
    if section == "user":
        return {k: v for k, v in data.items() if k in USER_PROPERTIES}
    if section in {"grants", "tokens"}:
        allowed = GRANT_FIELDS if section == "grants" else TOKEN_FIELDS
        return [{k: v for k, v in row.items() if k in allowed} for row in data]
    if section == "authentication":
        return {
            "policy": data.get("policy"),
            "methods": data.get("methods", []),
            "properties": {
                k: v for k, v in data.get("properties", {}).items() if k in AUTH_PROPERTIES
            },
        }
    if section == "network":
        return {
            "policy": data.get("policy"),
            "level": data.get("level"),
            "scope": data.get("scope"),
            "properties": {
                k: v for k, v in data.get("properties", {}).items() if k in NETWORK_PROPERTIES
            },
            "rules": [
                {k: v for k, v in r.items() if k in {"name", "action", "type", "mode", "values"}}
                for r in data.get("rules", [])
            ],
        }
    if section == "role_access":
        return {
            "role": data.get("role"),
            "grants": [
                {
                    k: v
                    for k, v in row.items()
                    if k in {"privilege", "granted_on", "name", "grant_option"}
                }
                for row in data.get("grants", [])
            ],
        }
    if section == "keys":
        return [
            {
                k: v
                for k, v in row.items()
                if k
                in {
                    "name",
                    "user_name",
                    "fingerprint",
                    "role_scope",
                    "status",
                    "created_on",
                    "last_used_on",
                    "expires_at",
                }
            }
            for row in data
        ]
    if section == "workload":
        return [
            {
                "name": row.get("name"),
                "type": row.get("type"),
                "last_used": row.get("last_used"),
                "additional_info": {
                    k: v
                    for k, v in row.get("additional_info", {}).items()
                    if k
                    in {
                        "awsPartition",
                        "awsAccount",
                        "type",
                        "iamRole",
                        "issuer",
                        "subject",
                        "audienceList",
                    }
                },
            }
            for row in data
        ]
    if section == "login":
        return {
            k: v
            for k, v in data.items()
            if k in {"at", "ip", "method", "success", "scope", "event", "note"}
        }
    raise ValueError("Unknown security metadata section.")
