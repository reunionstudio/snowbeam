"""Read-only local Snowflake credential hygiene scanner."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .config import Config, ConfigError, read_document
from .snow_paths import config_files_to_scan

SECRET_FIELD_NAMES = frozenset(
    {
        "password",
        "token",
        "private_key",
        "private_key_passphrase",
        "private_key_pwd",
        "passphrase",
        "secret",
        "access_token",
    }
)

ENV_SECRET_VARS = frozenset(
    {
        "PRIVATE_KEY_PASSPHRASE",
        "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_TOKEN",
        "SNOWSQL_PWD",
    }
)

SHELL_STARTUP_FILES = (
    Path.home() / ".zshrc",
    Path.home() / ".zprofile",
    Path.home() / ".bashrc",
    Path.home() / ".bash_profile",
    Path.home() / ".profile",
)

EXPORT_PATTERN = re.compile(
    r"(?m)^\s*(?:export\s+)?(" + "|".join(ENV_SECRET_VARS) + r")\s*=",
    re.IGNORECASE,
)


def _finding(
    *,
    check: str,
    severity: str,
    message: str,
    location: str,
    fix: str,
    connection: str | None = None,
) -> dict:
    return {
        "check": check,
        "severity": severity,
        "message": message,
        "location": location,
        "fix": fix,
        "connection": connection,
    }


def _mode_too_open(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return (mode & 0o077) != 0


def _looks_like_linux_path_on_mac(path: str) -> bool:
    return sys.platform == "darwin" and path.startswith("/home/")


def _connection_tables(document, source: Path) -> dict:
    if source.name == "connections.toml":
        return document if isinstance(document, dict) else {}
    connections = document.get("connections") if isinstance(document, dict) else None
    return connections if isinstance(connections, dict) else {}


def _scan_toml_secrets(path: Path, kind: str) -> list[dict]:
    findings: list[dict] = []
    try:
        document = read_document(path)
    except (ConfigError, OSError, ValueError):
        findings.append(
            _finding(
                check="config_unreadable",
                severity="high",
                message=f"Could not parse {path}.",
                location=str(path),
                fix="Repair the file syntax so Snowflake CLI can read it.",
            )
        )
        return findings
    if _mode_too_open(path):
        findings.append(
            _finding(
                check="permissions",
                severity="medium",
                message=f"{path} permissions are looser than 0600 (group/other bits set).",
                location=str(path),
                fix=f"Run: chmod 600 {path}",
            )
        )
    entries = _connection_tables(document, path)
    for name, values in entries.items():
        if not isinstance(values, dict):
            continue
        for key in values:
            if key.lower() in SECRET_FIELD_NAMES:
                findings.append(
                    _finding(
                        check="plaintext_secret_field",
                        severity="high",
                        message=(
                            f"Connection {name!r} stores {key!r} in {path.name}. "
                            "Snowbeam never prints the value."
                        ),
                        location=str(path),
                        fix=(
                            "Remove the secret from the file. Use Keychain via "
                            "snowbeam secrets set-passphrase and snowbeam snow."
                        ),
                        connection=str(name),
                    )
                )
        key_path = values.get("private_key_file") or values.get("private_key_path")
        inline_pass = values.get("private_key_passphrase") or values.get("private_key_pwd")
        if key_path and inline_pass:
            findings.append(
                _finding(
                    check="passphrase_in_config",
                    severity="high",
                    message=(
                        f"Connection {name!r} stores a private-key passphrase beside the key path."
                    ),
                    location=str(path),
                    fix="Remove the passphrase field; use snowbeam secrets set-passphrase.",
                    connection=str(name),
                )
            )
        if key_path:
            expanded = Path(str(key_path)).expanduser()
            if _looks_like_linux_path_on_mac(str(key_path)):
                findings.append(
                    _finding(
                        check="foreign_key_path",
                        severity="medium",
                        message=(
                            f"Connection {name!r} references a Linux-style path "
                            f"on macOS: {key_path}"
                        ),
                        location=str(path),
                        fix="Point private_key_file at a key that exists on this Mac.",
                        connection=str(name),
                    )
                )
            if not expanded.is_file():
                findings.append(
                    _finding(
                        check="missing_key_file",
                        severity="medium",
                        message=f"Connection {name!r} private key file is missing: {key_path}",
                        location=str(path),
                        fix="Copy the key to this machine or update the path.",
                        connection=str(name),
                    )
                )
            elif expanded.is_file():
                findings.extend(_scan_key_file(expanded, connection=str(name)))
                if _mode_too_open(expanded):
                    findings.append(
                        _finding(
                            check="permissions",
                            severity="medium",
                            message=f"Private key {expanded} permissions are looser than 0600.",
                            location=str(expanded),
                            fix=f"Run: chmod 600 {expanded}",
                            connection=str(name),
                        )
                    )
    return findings


def _scan_key_file(path: Path, *, connection: str) -> list[dict]:
    findings: list[dict] = []
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return findings
    if "BEGIN ENCRYPTED PRIVATE KEY" in text or "BEGIN RSA PRIVATE KEY" in text:
        return findings
    if "BEGIN PRIVATE KEY" in text:
        findings.append(
            _finding(
                check="unencrypted_private_key",
                severity="high",
                message=f"Private key file appears unencrypted: {path}",
                location=str(path),
                fix="Generate an encrypted PKCS#8 key and store the passphrase in Keychain.",
                connection=connection,
            )
        )
    return findings


def _scan_shared_keys(config: Config) -> list[dict]:
    from .hygiene import connection_hygiene

    findings: list[dict] = []
    for issue in connection_hygiene(config):
        if issue["kind"] == "shared_key_file":
            findings.append(
                _finding(
                    check="shared_key_file",
                    severity="medium",
                    message=issue["message"],
                    location=issue["path"],
                    fix=(
                        "Use separate key files per Snowflake user, or accept shared "
                        "custody consciously."
                    ),
                )
            )
    return findings


def _scan_environment() -> list[dict]:
    findings: list[dict] = []
    for name in ENV_SECRET_VARS:
        if name in os.environ and os.environ[name]:
            findings.append(
                _finding(
                    check="environment_secret",
                    severity="high",
                    message=f"{name} is set in this process environment.",
                    location="environment",
                    fix=(
                        f"Unset {name} in shell startup files and use snowbeam snow so only "
                        "the child process receives the passphrase."
                    ),
                )
            )
    return findings


def _scan_shell_startup() -> list[dict]:
    findings: list[dict] = []
    for path in SHELL_STARTUP_FILES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in EXPORT_PATTERN.finditer(text):
            var = match.group(1).upper()
            findings.append(
                _finding(
                    check="shell_export",
                    severity="high",
                    message=f"{var} is exported from {path.name}.",
                    location=str(path),
                    fix=(
                        f"Remove the export line from {path.name}, restart terminals and agents, "
                        "and use snowbeam secrets set-passphrase with snowbeam snow."
                    ),
                )
            )
    return findings


def scan_local_machine(
    config: Config | None = None,
    *,
    config_path: Path | None = None,
) -> dict:
    """Run all checks. Never prints or logs secret values."""
    cfg = config or Config(config_path)
    findings: list[dict] = []
    seen_paths: set[Path] = set()
    for entry in config_files_to_scan(cfg.path):
        path = entry["path"]
        if path in seen_paths:
            continue
        seen_paths.add(path)
        findings.extend(_scan_toml_secrets(path, entry["kind"]))
    findings.extend(_scan_shared_keys(cfg))
    findings.extend(_scan_environment())
    findings.extend(_scan_shell_startup())
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda row: (severity_rank.get(row["severity"], 9), row["check"]))
    return {
        "clean": not findings,
        "finding_count": len(findings),
        "findings": findings,
        "snowflake_config": str(cfg.path),
        "snowflake_home": str(cfg.path.parent),
    }


def scan_exit_code(report: dict) -> int:
    if report["clean"]:
        return 0
    if any(row["severity"] == "high" for row in report["findings"]):
        return 2
    return 1
