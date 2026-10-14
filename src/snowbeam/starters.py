"""Additive role starters. Never revoke or widen an existing role's grants."""

from __future__ import annotations

from .fleet import identifier, quote

STARTER_FIELDS = {"database", "schema", "stage", "warehouse"}


def account_key(organization: str, account: str) -> str:
    return f"{identifier(organization)}-{identifier(account)}"


BUILTIN_STARTERS: dict[str, dict] = {
    "inspect-only": {
        "title": "Inspect-only metadata (account-dependent)",
        "description": "MONITOR on account and SNOWFLAKE imported privileges for SHOW-style work.",
        "grants": [
            "GRANT MONITOR ON ACCOUNT TO ROLE {role}",
            "GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE {role}",
        ],
    },
    "read-database": {
        "title": "Read one database",
        "description": "USAGE on database and all schemas; SELECT on future tables in each schema.",
        "requires": ["database"],
        "grants": [
            "GRANT USAGE ON DATABASE {database} TO ROLE {role}",
            "GRANT USAGE ON ALL SCHEMAS IN DATABASE {database} TO ROLE {role}",
            "GRANT SELECT ON ALL TABLES IN DATABASE {database} TO ROLE {role}",
        ],
    },
    "load-stage": {
        "title": "Load files to one stage",
        "description": "USAGE on warehouse, database, schema, and WRITE on one stage.",
        "requires": ["database", "schema", "stage", "warehouse"],
        "grants": [
            "GRANT USAGE ON WAREHOUSE {warehouse} TO ROLE {role}",
            "GRANT USAGE ON DATABASE {database} TO ROLE {role}",
            "GRANT USAGE ON SCHEMA {database}.{schema} TO ROLE {role}",
            "GRANT WRITE ON STAGE {database}.{schema}.{stage} TO ROLE {role}",
        ],
    },
}

AIRLOCK_STARTERS: dict[str, dict] = {
    "airlock-user": {
        "title": "Airlock thin user role (optional pack)",
        "description": "Creates an AIRLOCK_USER-style role without table grants in Snowflake.",
        "grants": [],
    },
}


def list_starters(preferences: dict, user_starters: dict) -> dict[str, dict]:
    merged = dict(BUILTIN_STARTERS)
    if airlock_pack_enabled(preferences):
        merged.update(AIRLOCK_STARTERS)
    for key, value in user_starters.items():
        merged[key] = value
    return merged


def airlock_pack_enabled(preferences: dict) -> bool:
    return bool(preferences.get("airlock_pack"))


def expand_starter(
    starter_id: str,
    role: str,
    *,
    preferences: dict,
    user_starters: dict,
    params: dict[str, str],
) -> list[str]:
    catalog = list_starters(preferences, user_starters)
    if starter_id not in catalog:
        raise ValueError(f"Unknown role starter: {starter_id}")
    spec = catalog[starter_id]
    role = identifier(role)
    for field in spec.get("requires", []):
        if not params.get(field):
            raise ValueError(f"Starter {starter_id} requires --{field.replace('_', '-')}.")
    statements = []
    for template in spec.get("grants", []):
        formatted = template.format(
            role=quote(role),
            database=quote(identifier(params["database"])) if params.get("database") else "",
            schema=quote(identifier(params["schema"])) if params.get("schema") else "",
            stage=quote(identifier(params["stage"])) if params.get("stage") else "",
            warehouse=quote(identifier(params["warehouse"])) if params.get("warehouse") else "",
        )
        statements.append(formatted)
    if not any(s.upper().startswith("CREATE ROLE") for s in statements):
        statements.insert(0, f"CREATE ROLE IF NOT EXISTS {quote(role)}")
    return statements


def validate_user_starter(name: str, data: dict) -> dict:
    if not isinstance(data.get("grants"), list) or not data["grants"]:
        raise ValueError("A saved starter needs a non-empty grants list.")
    for item in data["grants"]:
        if not isinstance(item, str) or len(item) > 2048:
            raise ValueError("Starter grants must be short SQL strings.")
    result = {
        "title": str(data.get("title", name)),
        "description": str(data.get("description", "")),
        "grants": list(data["grants"]),
    }
    if data.get("requires"):
        result["requires"] = [str(x) for x in data["requires"]]
    return result
