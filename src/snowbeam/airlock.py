"""Optional Airlock-style thin Snowflake roles. Snowbeam does not depend on Airlock."""

from __future__ import annotations

import os
import re

AIRLOCK_ROLE_PATTERN = re.compile(r"^AIRLOCK[_A-Z0-9]*$", re.I)
WRITE_PRIVILEGES = {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "OWNERSHIP"}


def airlock_pack_enabled(preferences: dict) -> bool:
    return bool(preferences.get("airlock_pack"))


def detect_airlock_context(grants: list[dict], role_name: str) -> dict:
    """Return hints when a role matches a deliberately thin Airlock user pattern."""
    hints = []
    upper = role_name.upper()
    env_hint = any(k.startswith("AIRLOCK_") for k in os.environ)
    if AIRLOCK_ROLE_PATTERN.match(upper) or env_hint:
        hints.append(
            {
                "kind": "airlock_thin_role",
                "message": (
                    "This role matches an Airlock-style pattern. Fine-grained table access "
                    "may be enforced inside Airlock rather than through additional Snowflake "
                    "grants. Snowbeam will not suggest widening this role for Airlock paths."
                ),
            }
        )
    direct_table_writes = [
        g
        for g in grants
        if g.get("granted_on") == "TABLE"
        and str(g.get("privilege", "")).upper() in WRITE_PRIVILEGES
    ]
    if direct_table_writes and not AIRLOCK_ROLE_PATTERN.match(upper):
        hints.append(
            {
                "kind": "direct_table_write",
                "message": (
                    "This agent role has direct write privileges on tables. A governed write "
                    "path (for example Airlock) is one option; review whether direct writes "
                    "match your intent."
                ),
                "grants": [
                    {k: g[k] for k in ("privilege", "granted_on", "name") if k in g}
                    for g in direct_table_writes[:5]
                ],
            }
        )
    return {"hints": hints, "suppress_grant_suggestions": AIRLOCK_ROLE_PATTERN.match(upper)}
