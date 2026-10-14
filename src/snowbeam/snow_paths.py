"""Discover Snowflake CLI configuration locations on this machine."""

from __future__ import annotations

from pathlib import Path

from .config import default_config_path


def snowflake_config_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().absolute()
    return default_config_path()


def snowflake_home(config_path: Path) -> Path:
    return config_path.parent


def snowsql_config_path() -> Path:
    return Path.home() / ".snowsql" / "config"


def config_files_to_scan(config_path: Path | None = None) -> list[dict]:
    """Return metadata for each local file the hygiene scanner may read."""
    main = snowflake_config_path(config_path)
    home = snowflake_home(main)
    files: list[dict] = []
    for path in (main, home / "connections.toml"):
        if path.is_file():
            files.append({"kind": "snowflake_cli", "path": path})
    legacy = Path.home() / ".snowflake" / "config.toml"
    if legacy.is_file() and legacy != main:
        files.append({"kind": "snowflake_cli_legacy", "path": legacy})
        legacy_conn = legacy.with_name("connections.toml")
        if legacy_conn.is_file():
            files.append({"kind": "snowflake_cli_legacy", "path": legacy_conn})
    snowsql = snowsql_config_path()
    if snowsql.is_file():
        files.append({"kind": "snowsql", "path": snowsql})
    return files
