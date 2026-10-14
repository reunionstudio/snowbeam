"""Map Snowflake DDL/DCL to the narrowest built-in administrative role typically required."""

from __future__ import annotations

import re

PRIVILEGED = ("ACCOUNTADMIN", "ORGADMIN", "GLOBALORGADMIN")


def minimum_admin_role(sql: str) -> str:
    text = sql.strip().upper()
    if (
        re.search(r"\bCREATE\s+(USER|ROLE)\b", text)
        or re.search(r"\bALTER\s+USER\b", text)
        or re.search(r"\bGRANT\s+ROLE\b", text)
    ):
        return "USERADMIN"
    if (
        "AUTHENTICATION POLICY" in text
        or "NETWORK POLICY" in text
        or "NETWORK RULE" in text
        or re.search(r"\bCREATE\s+SECURITY\s+", text)
    ):
        return "SECURITYADMIN"
    if re.search(r"\bGRANT\b", text) and " ON " in text:
        return "SECURITYADMIN"
    if re.search(r"\bCREATE\s+(DATABASE|SCHEMA|TABLE|VIEW|STAGE|WAREHOUSE)\b", text):
        return "SYSADMIN"
    return "SECURITYADMIN"


def accountadmin_warning(connection_role: str | None) -> str | None:
    if connection_role and connection_role.upper() in PRIVILEGED:
        return (
            "The marked admin connection is signed in with a broad role "
            f"({connection_role.upper()}). Snowflake may allow this, but prefer "
            "USERADMIN, SECURITYADMIN, or SYSADMIN scoped to each step."
        )
    return None
