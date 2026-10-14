"""Read-only connection hygiene checks on local Snowflake profile metadata."""

from __future__ import annotations

from pathlib import Path

from .config import Config


def connection_hygiene(config: Config) -> list[dict]:
    issues = []
    key_paths: dict[str, list[str]] = {}
    for profile in config.profiles(environment=False):
        path = profile.settings.get("private_key_file") or profile.settings.get("private_key_path")
        if not path:
            continue
        key_paths.setdefault(path, []).append(profile.name)
        if not Path(path).expanduser().is_file():
            issues.append(
                {
                    "kind": "missing_key_file",
                    "connection": profile.name,
                    "path": path,
                    "message": "The configured private key file does not exist on this machine.",
                }
            )
    for path, names in key_paths.items():
        if len(names) > 1:
            issues.append(
                {
                    "kind": "shared_key_file",
                    "connections": names,
                    "path": path,
                    "message": "Multiple connections reference the same private key file path.",
                }
            )
    return issues
