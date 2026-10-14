"""Snowbeam-owned connection classification (admin vs agent)."""

from __future__ import annotations

from pathlib import Path

import tomlkit

from .config import ConfigError, atomic_write, read_document

CLASS_ADMIN = "admin"
CLASS_AGENT = "agent"
VALID_CLASSES = frozenset({CLASS_ADMIN, CLASS_AGENT})


class ConnectionAccess:
    """Explicit metadata: which Snowflake connections are human-admin vs agent-scoped."""

    def __init__(self, state_dir: Path):
        self.path = state_dir.expanduser() / "connection_access.toml"

    def _document(self):
        if not self.path.exists():
            return tomlkit.document()
        document = read_document(self.path)
        if "connections" in document and not isinstance(document["connections"], dict):
            raise ConfigError("connection_access.toml: [connections] must be a table.")
        return document

    def classification(self, connection: str) -> str:
        """Return admin or agent. Unlisted connections are admin (fail closed for agents)."""
        table = self._document().get("connections", {})
        if not isinstance(table, dict):
            return CLASS_ADMIN
        value = table.get(connection)
        if value is None:
            return CLASS_ADMIN
        text = str(value).strip().lower()
        if text not in VALID_CLASSES:
            raise ConfigError(
                f"Invalid class {text!r} for connection {connection!r} in {self.path}."
            )
        return text

    def set_classification(self, connection: str, role: str) -> None:
        role = role.strip().lower()
        if role not in VALID_CLASSES:
            raise ConfigError(f"Role must be {CLASS_ADMIN!r} or {CLASS_AGENT!r}.")
        document = self._document()
        document.setdefault("connections", {})[connection] = role
        atomic_write(self.path, tomlkit.dumps(document))

    def remove_classification(self, connection: str) -> None:
        document = self._document()
        table = document.get("connections", {})
        if isinstance(table, dict):
            table.pop(connection, None)
        atomic_write(self.path, tomlkit.dumps(document))

    def all_classifications(self) -> dict[str, str]:
        table = self._document().get("connections", {})
        if not isinstance(table, dict):
            return {}
        result: dict[str, str] = {}
        for name, value in table.items():
            role = str(value).strip().lower()
            if role in VALID_CLASSES:
                result[str(name)] = role
        return result

    def visible_connection_names(
        self, master_names: list[str], *, agent_context: bool
    ) -> list[str]:
        if agent_context:
            return [name for name in master_names if self.classification(name) == CLASS_AGENT]
        return list(master_names)

    def connection_allowed(self, connection: str | None, *, agent_context: bool) -> bool:
        if not connection:
            return not agent_context
        role = self.classification(connection)
        if agent_context:
            return role == CLASS_AGENT
        return True

    def requires_admin_challenge(self, connection: str | None) -> bool:
        if not connection:
            return False
        return self.classification(connection) == CLASS_ADMIN
