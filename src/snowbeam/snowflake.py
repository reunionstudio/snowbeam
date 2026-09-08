"""A narrow Snowflake CLI adapter. Commands never go through a shell."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime

from .config import Profile

IDENTITY_SQL = """SELECT CURRENT_ORGANIZATION_NAME() AS ORGANIZATION_NAME,
CURRENT_ACCOUNT_NAME() AS ACCOUNT_NAME, CURRENT_ACCOUNT() AS ACCOUNT_LOCATOR,
CURRENT_REGION() AS REGION, CURRENT_USER() AS USER_NAME,
CURRENT_ROLE() AS ROLE_NAME, CURRENT_WAREHOUSE() AS WAREHOUSE_NAME"""
PAT_SQL = "SHOW USER PROGRAMMATIC ACCESS TOKENS"
ACCOUNTS_SQL = "SHOW ACCOUNTS"
SNAPSHOT_SQL = IDENTITY_SQL + "; " + PAT_SQL


class SnowError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def timestamp(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            return None  # Guessing a timezone could make an expiry reminder late.
        return date.astimezone(UTC).isoformat()
    except ValueError:
        return None


def parse_rows(output: str) -> list[dict]:
    try:
        rows = json.loads(output)
    except (ValueError, TypeError):
        raise SnowError("output", "Snowflake CLI did not return valid JSON metadata.") from None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise SnowError("output", "Snowflake CLI returned an unexpected metadata format.")
    return [{key.lower(): value for key, value in row.items()} for row in rows]


def safe_error(output: str) -> SnowError:
    """Classify errors without displaying raw output, which may contain credentials."""
    lowered = output.lower()
    if "network policy" in lowered or "not allowed to access snowflake" in lowered:
        return SnowError(
            "network_policy", "Snowflake blocked this network under its access policy."
        )
    if "expired" in lowered and any(
        word in lowered for word in ("token", "password", "credential")
    ):
        return SnowError("expired", "Snowflake reports an expired credential. Sign in another way.")
    if any(
        word in lowered for word in ("insufficient privileges", "not authorized", "access denied")
    ):
        return SnowError("permission", "The current role cannot inspect this metadata.")
    if "unable to configure handler" in lowered:
        return SnowError(
            "cli_config", "Snowflake CLI cannot open its log file. Check CLI configuration."
        )
    if any(
        word in lowered for word in ("could not connect", "failed to connect", "name resolution")
    ):
        return SnowError(
            "network", "Cannot reach Snowflake. Check the account address and network."
        )
    if any(word in lowered for word in ("authentication", "incorrect username", "jwt token")):
        return SnowError(
            "authentication", "Authentication failed. Check credentials and account policy."
        )
    return SnowError(
        "query", "Snowflake CLI failed. Use snow connection test for further diagnosis."
    )


class SnowClient:
    def __init__(self, executable: str | None = None, timeout: int = 45):
        self.executable = executable or shutil.which("snow")
        self.timeout = timeout

    def _output(self, profile: Profile, sql: str, *, interactive: bool = True) -> str:
        if not self.executable:
            raise SnowError("missing_cli", "Install Snowflake CLI: uv tool install snowflake-cli")
        if not interactive and not profile.background_safe:
            raise SnowError("sign_in", "Interactive sign-in required; use Refresh in Snowbeam.")
        command = [
            self.executable,
            "--config-file",
            str(profile.config_path),
            "sql",
            "--connection",
            profile.name,
            "--query",
            sql,
            "--format",
            "JSON",
            "--silent",
        ]
        environment = os.environ.copy()
        # The connector independently locates connections.toml via SNOWFLAKE_HOME.
        # Keep it alongside the selected config, including on older CLI releases.
        environment["SNOWFLAKE_HOME"] = str(profile.config_path.parent)
        if not interactive:
            command.extend(["--authenticator", profile.auth])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=environment,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SnowError(
                "timeout", "Snowflake did not respond in time. Cached metadata is retained."
            ) from None
        except OSError:
            raise SnowError(
                "missing_cli", "Cannot start Snowflake CLI. Check its installation."
            ) from None
        if result.returncode:
            raise safe_error(result.stderr + result.stdout)
        return result.stdout

    def query(self, profile: Profile, sql: str, *, interactive: bool = True) -> list[dict]:
        return parse_rows(self._output(profile, sql, interactive=interactive))

    @staticmethod
    def _identity(rows: list[dict]) -> dict:
        required = ("organization_name", "account_name", "account_locator", "region", "user_name")
        if len(rows) != 1 or any(not rows[0].get(key) for key in required):
            raise SnowError("output", "Snowflake returned incomplete account identity metadata.")
        return rows[0]

    def snapshot(self, profile: Profile, *, interactive: bool = True) -> tuple[dict, list[dict]]:
        # Both result sets belong to one authenticated session, even if a profile or
        # redirect changes during refresh. This also avoids a second browser login.
        output = self._output(profile, SNAPSHOT_SQL, interactive=interactive)
        try:
            sets = json.loads(output)
        except ValueError:
            raise SnowError("output", "Snowflake CLI did not return valid JSON metadata.") from None
        if not isinstance(sets, list) or len(sets) != 2:
            raise SnowError("output", "Expected identity and token metadata from one session.")
        identity = self._identity(parse_rows(json.dumps(sets[0])))
        tokens = parse_rows(json.dumps(sets[1]))
        if any(
            not row.get("name") or row.get("user_name") != identity["user_name"] for row in tokens
        ):
            raise SnowError("output", "Token inventory does not match the authenticated user.")
        return identity, tokens

    def identity(self, profile: Profile, *, interactive: bool = True) -> dict:
        rows = self.query(profile, IDENTITY_SQL, interactive=interactive)
        return self._identity(rows)

    def accounts(self, profile: Profile, *, interactive: bool = True) -> list[dict]:
        rows = self.query(profile, ACCOUNTS_SQL, interactive=interactive)
        if any(not row.get("organization_name") or not row.get("account_name") for row in rows):
            raise SnowError("output", "Snowflake returned incomplete organization metadata.")
        return rows


def safe_text(value: object) -> str:
    """Render metadata literally and remove terminal control sequences."""
    text = "" if value is None else str(value)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(char for char in text if ord(char) >= 32 and ord(char) != 127)
