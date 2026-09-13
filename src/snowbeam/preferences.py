"""Local application preferences, separate from Snowflake credentials."""

from pathlib import Path

import tomlkit

from .config import ConfigError, atomic_write, read_document


class Preferences:
    def __init__(self, directory: Path):
        self.path = directory / "preferences.toml"

    def read(self) -> dict:
        document = read_document(self.path)
        if not isinstance(document.get("updates", {}), dict) or not isinstance(
            document.get("refresh", {}), dict
        ):
            raise ConfigError("Invalid Snowbeam preferences. Expected updates and refresh tables.")
        return document

    def automatic_updates(self) -> bool:
        value = self.read().get("updates", {}).get("automatic", False)
        if not isinstance(value, bool):
            raise ConfigError("The automatic update-check preference must be true or false.")
        return value

    def set_automatic_updates(self, enabled: bool) -> None:
        document = self.read()
        document.setdefault("updates", {})["automatic"] = enabled
        atomic_write(self.path, tomlkit.dumps(document))

    def refresh_minutes(self, connection_key: str) -> int:
        value = self.read().get("refresh", {}).get(connection_key, 60)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 1440:
            raise ConfigError("Refresh intervals must be whole minutes from 0 to 1440.")
        return int(value)

    def set_refresh_minutes(self, connection_key: str, minutes: int) -> None:
        if type(minutes) is not int or not 0 <= minutes <= 1440:
            raise ConfigError("Refresh intervals must be whole minutes from 0 to 1440.")
        document = self.read()
        document.setdefault("refresh", {})[connection_key] = minutes
        atomic_write(self.path, tomlkit.dumps(document))
