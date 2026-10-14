"""Capability probes for post-provision evidence runs. Suggestions only; never auto-apply."""

from __future__ import annotations

from .fleet import quote
from .snowflake import SnowError

# Maps request capability ids to a probe SQL and optional human suggestion on failure.
PROBES: dict[str, tuple[str, str]] = {
    "show_grants": (
        "SHOW GRANTS TO USER {user}",
        "Grant the role visibility on its own grants, or grant MONITOR on the account for "
        "broader metadata commands.",
    ),
    "show_users": (
        "SHOW USERS",
        "Grant MONITOR ON ACCOUNT to the agent role (or a narrower metadata role your "
        "account allows for SHOW USERS).",
    ),
    "show_roles": (
        "SHOW ROLES",
        "Grant MONITOR ON ACCOUNT or IMPORTED PRIVILEGES on SNOWFLAKE as your account "
        "requires for SHOW ROLES.",
    ),
    "show_tables": (
        "SHOW TABLES",
        "Grant USAGE on the database and schema, and ensure the role can see the target schema.",
    ),
    "select_tables": (
        "SELECT 1",
        "Grant SELECT on the required tables or views, plus USAGE on their database and schema.",
    ),
}


def normalize_capabilities(values: list[str]) -> list[str]:
    result = []
    for item in values:
        key = item.strip().lower().replace(" ", "_").replace("-", "_")
        if key.startswith("show "):
            key = key.replace(" ", "_")
        if key and key not in result:
            result.append(key)
    return result


def run_capability_checks(
    client, profile, organization: str, account: str, capabilities: list[str]
):
    from .security import guarded_query

    checks = []
    for cap in normalize_capabilities(capabilities):
        if cap not in PROBES:
            checks.append(
                {
                    "capability": cap,
                    "status": "skipped",
                    "message": "No built-in probe for this capability; review manually.",
                    "suggested_grant": None,
                }
            )
            continue
        template, suggestion = PROBES[cap]
        sql = template.format(user=quote(profile.settings["user"]))
        try:
            guarded_query(client, profile, organization, account, sql, interactive=True)
            checks.append(
                {
                    "capability": cap,
                    "status": "passed",
                    "message": f"Succeeded: {cap.replace('_', ' ')}",
                    "suggested_grant": None,
                }
            )
        except SnowError as exc:
            checks.append(
                {
                    "capability": cap,
                    "status": "failed",
                    "message": str(exc),
                    "suggested_grant": suggestion,
                }
            )
    return checks
