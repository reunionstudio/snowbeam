"""Read Snowflake's existing configuration; edit only fields Snowdock owns."""

from __future__ import annotations

import fcntl
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from platformdirs import user_config_path

FIELDS = (
    "account",
    "user",
    "authenticator",
    "role",
    "warehouse",
    "database",
    "schema",
    "host",
    "token_file_path",
    "private_key_file",
)
AUTH_METHODS = ("externalbrowser", "PROGRAMMATIC_ACCESS_TOKEN", "SNOWFLAKE_JWT")


class ConfigError(Exception):
    """An actionable configuration problem, containing no secret values."""


def default_config_path() -> Path:
    if value := os.environ.get("SNOWFLAKE_HOME"):
        return Path(value).expanduser() / "config.toml"
    legacy = Path.home() / ".snowflake"
    if legacy.is_dir():
        return legacy / "config.toml"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/snowflake/config.toml"
    return user_config_path("snowflake", appauthor=False) / "config.toml"


def read_document(path: Path):
    try:
        return tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    except (OSError, UnicodeError, tomlkit.exceptions.ParseError):
        raise ConfigError(f"Cannot read valid TOML from {path}. Fix the file and retry.") from None


def atomic_write(path: Path, content: str) -> None:
    """Replace a private file without leaving a partially written configuration."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ConfigError(f"Refusing to replace symlink {path}; edit its target directly.")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Profile:
    name: str
    config_path: Path
    source_path: Path
    settings: dict[str, str]
    is_default: bool = False

    @property
    def key(self) -> str:
        return f"{self.config_path.absolute()}::{self.name}"

    @property
    def auth(self) -> str:
        return self.settings.get("authenticator", "snowflake")

    @property
    def background_safe(self) -> bool:
        return self.auth.upper() in {"PROGRAMMATIC_ACCESS_TOKEN", "SNOWFLAKE_JWT"}


class Config:
    def __init__(self, path: Path | None = None):
        self.path = (path or default_config_path()).expanduser().absolute()

    @property
    def source(self) -> Path:
        connections = self.path.with_name("connections.toml")
        return connections if connections.exists() else self.path

    def profiles(self, *, environment: bool = True) -> list[Profile]:
        config = read_document(self.path)
        source = self.source
        data = read_document(source)
        entries = data if source != self.path else data.get("connections", {})
        if not isinstance(entries, dict):
            raise ConfigError(f"Expected connection tables in {source}.")
        default = config.get("default_connection_name", "")
        if environment:
            default = os.environ.get("SNOWFLAKE_DEFAULT_CONNECTION_NAME", default)
        profiles = []
        for name, values in entries.items():
            if not isinstance(values, dict):
                raise ConfigError(f"Expected named connection tables in {source}.")
            settings = {key: str(values[key]) for key in FIELDS if key in values}
            # The connector also accepts private_key_path. Keep its public path visible.
            if "private_key_file" not in settings and "private_key_path" in values:
                settings["private_key_file"] = str(values["private_key_path"])
            if environment:
                for key in FIELDS:
                    override = f"SNOWFLAKE_CONNECTIONS_{name.upper()}_{key.upper()}"
                    generic = f"SNOWFLAKE_{key.upper()}"
                    if override in os.environ:
                        settings[key] = os.environ[override]
                    elif key not in settings and generic in os.environ:
                        settings[key] = os.environ[generic]
            profiles.append(Profile(name, self.path, source, settings, name == default))
        return sorted(profiles, key=lambda item: item.name.casefold())

    def profile(self, name: str, *, environment: bool = True) -> Profile:
        for profile in self.profiles(environment=environment):
            if profile.name == name:
                return profile
        raise ConfigError(f"Connection {name!r} does not exist in {self.source}.")

    @contextmanager
    def _edit(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = path.with_name(f".{path.name}.snowdock.lock")
        if lock_path.is_symlink():
            raise ConfigError(f"Refusing to use symlink {lock_path} as a lock.")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.is_symlink():
                raise ConfigError(f"Refusing to replace symlink {path}; edit its target directly.")
            original = path.read_bytes() if path.exists() else None
            document = read_document(path)
            yield document
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise ConfigError("Configuration changed in another program. Reload and retry.")
            if original is not None:
                atomic_write(path.with_name(path.name + ".snowdock.bak"), original.decode())
            atomic_write(path, tomlkit.dumps(document))

    def save(self, name: str, settings: dict[str, str], *, create: bool = False) -> None:
        if create and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ConfigError("Use letters, digits, dots, underscores, or hyphens for the name.")
        if any(key not in FIELDS for key in settings):
            raise ConfigError("Only connection settings and credential file paths can be edited.")
        for value in settings.values():
            if any(ord(char) < 32 for char in value):
                raise ConfigError("Connection fields cannot contain control characters.")
        for field in ("token_file_path", "private_key_file"):
            value = settings.get(field, "")
            if value and not (value.startswith("/") or value.startswith("~/")):
                raise ConfigError(
                    "Credential file paths must start with / or ~/. Enter a path, not a token."
                )
        source = self.source
        with self._edit(source) as document:
            if source == self.path:
                if "connections" not in document:
                    document["connections"] = tomlkit.table()
                entries = document["connections"]
            else:
                entries = document
            if create and name in entries:
                raise ConfigError("That connection name already exists.")
            if not create and name not in entries:
                raise ConfigError("That connection no longer exists. Reload and retry.")
            if create:
                entries[name] = tomlkit.table()
            values = entries[name]
            if "private_key_file" in settings:
                values.pop("private_key_path", None)
            for key, value in settings.items():
                if value:
                    values[key] = value
                else:
                    values.pop(key, None)
            if not values.get("account") or not values.get("user"):
                raise ConfigError("Account and username are required.")

    def remove(self, name: str) -> None:
        self.profile(name)
        source = self.source
        with self._edit(source) as document:
            entries = document if source != self.path else document["connections"]
            entries.pop(name, None)
            if source == self.path and document.get("default_connection_name") == name:
                document.pop("default_connection_name", None)
        if source != self.path and read_document(self.path).get("default_connection_name") == name:
            with self._edit(self.path) as document:
                document.pop("default_connection_name", None)

    def set_default(self, name: str) -> None:
        self.profile(name)
        with self._edit(self.path) as document:
            document["default_connection_name"] = name
