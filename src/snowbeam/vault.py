"""Trusted vault adapters. Secret input and output stay in captured pipes."""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import tomlkit

from .config import Profile, atomic_write
from .fleet import REFERENCE_FIELDS, vault_values
from .snowflake import SnowError


class VaultError(SnowError):
    """A fixed, secret-free failure message."""

    def __init__(self, message: str):
        super().__init__("vault", message)


def _run(command: list[str], *, payload: str | None = None) -> dict:
    try:
        result = subprocess.run(
            command,
            input=payload,
            stdin=None if payload is not None else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise VaultError(
            "Vault command failed or timed out. Check the provider's CLI and unlock state."
        ) from None
    if result.returncode:
        raise VaultError(
            "Vault command was denied or failed. Check its account, permissions, and unlock state."
        )
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise VaultError(
            "Vault returned an unexpected response; no secret output was retained."
        ) from None
    if not isinstance(value, dict):
        raise VaultError("Vault returned an unexpected item format.")
    return value


class Vault:
    def __init__(self, scope: dict):
        vault_values(scope)
        self.scope = {k: str(v) for k, v in scope.items() if k in REFERENCE_FIELDS}
        self.provider = scope["provider"]
        self.executable = shutil.which("op" if self.provider == "onepassword" else "bw")
        if not self.executable:
            raise VaultError("Install and unlock the selected password manager's CLI first.")

    def command(self, *args: str) -> list[str]:
        if self.provider == "onepassword":
            return [self.executable, "--account", self.scope["vault_account"], *args]
        return [self.executable, *args]

    def ready(self) -> None:
        if self.provider == "onepassword":
            _run(self.command("vault", "get", self.scope["vault"], "--format", "json"))
        else:
            status = _run(self.command("status"))
            if (
                status.get("status") != "unlocked"
                or str(status.get("serverUrl", "")).rstrip("/")
                != self.scope["vault_server"].rstrip("/")
                or status.get("userId") != self.scope["vault_user"]
            ):
                raise VaultError(
                    "Bitwarden must be unlocked for the exact server and user recorded in the "
                    "template."
                )

    def read(self, reference: dict | None = None) -> dict[str, str]:
        reference = reference or self.scope
        item = reference.get("item", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{20,64}", item):
            raise VaultError("Use an immutable vault item ID.")
        self.ready()
        if self.provider == "onepassword":
            data = _run(
                self.command(
                    "item", "get", item, "--vault", self.scope["vault"], "--format", "json"
                )
            )
        else:
            data = _run(self.command("get", "item", item))
        result = self._fields(data, reference)
        if data.get("id") != item or not (result.get("token") or result.get("private_key")):
            raise VaultError(
                "The vault item is missing Snowbeam's credential fields or has a different ID."
            )
        return result

    def _fields(self, data: dict, reference: dict) -> dict[str, str]:
        kind = reference.get("credential_kind")
        desired = {
            "token": reference.get("token_field", "token"),
            "private_key": reference.get("key_field", "private_key"),
            "passphrase": reference.get("passphrase_field", "passphrase"),
        }
        if kind == "PAT":
            desired = {"token": desired["token"]}
        elif kind == "KEYPAIR":
            desired.pop("token")
        result = {}
        for target, selector in desired.items():
            matches = []
            fields = data.get("fields") or []
            if not isinstance(fields, list):
                raise VaultError("Vault returned invalid field metadata.")
            for field in fields:
                if not isinstance(field, dict):
                    raise VaultError("Vault returned invalid field metadata.")
                if any(
                    field.get(k) is not None and not isinstance(field[k], str)
                    for k in ("id", "label", "name")
                ):
                    raise VaultError("Vault returned invalid field metadata.")
                names = {field.get("id"), field.get("label"), field.get("name")}
                section = field.get("section") or {}
                if not isinstance(section, dict) or any(
                    section.get(k) is not None and not isinstance(section[k], str)
                    for k in ("id", "label")
                ):
                    raise VaultError("Vault returned invalid section metadata.")
                names |= {
                    f"{section.get(s)}/{field.get(f)}"
                    for s in ("id", "label")
                    for f in ("id", "label")
                    if section.get(s) and field.get(f)
                }
                if selector in names and isinstance(field.get("value"), str):
                    matches.append(field["value"])
            if (
                self.provider == "bitwarden"
                and selector == "password"
                and isinstance(data.get("login"), dict)
                and isinstance(data["login"].get("password"), str)
            ):
                matches.append(data["login"]["password"])
            if (
                self.provider == "bitwarden"
                and selector == "notes"
                and isinstance(data.get("notes"), str)
            ):
                matches.append(data["notes"])
            if len(matches) > 1:
                raise VaultError(
                    "The vault field reference is ambiguous. Select a unique field ID or "
                    "section/field."
                )
            if matches:
                result[target] = matches[0]
        if result.get("private_key") and "passphrase" not in result:
            result["passphrase"] = (
                ""  # Existing unencrypted keys are supported; new keys are encrypted.
            )
        if set(result) not in ({"token"}, {"private_key", "passphrase"}):
            raise VaultError(
                "Select one PAT or key-pair credential and its fields in the vault reference."
            )
        return result

    def create(self, title: str, secret: dict[str, str]) -> dict:
        self.ready()
        if set(secret) not in ({"token"}, {"private_key", "passphrase"}):
            raise VaultError("Unsupported credential payload.")
        if self.provider == "onepassword":
            item = {
                "title": title,
                "category": "SECURE_NOTE",
                "fields": [
                    {"id": k, "label": k, "type": "CONCEALED", "value": v}
                    for k, v in secret.items()
                ],
            }
            data = _run(
                self.command(
                    "item", "create", "-", "--vault", self.scope["vault"], "--format", "json"
                ),
                payload=json.dumps(item),
            )
        else:
            item = {
                "type": 2,
                "name": title,
                "secureNote": {"type": 0},
                "fields": [{"name": k, "value": v, "type": 1} for k, v in secret.items()],
                "notes": "Created by Snowbeam. Credential values are never cached by Snowbeam.",
                "organizationId": None,
                "collectionIds": [],
            }
            data = _run(
                self.command("create", "item"),
                payload=base64.b64encode(json.dumps(item).encode()).decode(),
            )
        item_id = data.get("id", "")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{20,64}", item_id):
            raise VaultError(
                "Vault write returned no usable item ID. Check the vault before retrying."
            )
        reference = {
            **self.scope,
            "item": item_id,
            "credential_kind": "PAT" if "token" in secret else "KEYPAIR",
        }
        # Never claim success until a separate read returns exactly what was written.
        actual = self.read(reference)
        if set(actual) != set(secret) or not all(
            hmac.compare_digest(actual[k], v) for k, v in secret.items()
        ):
            raise VaultError(
                "Vault verification failed. Check the newly created item before retrying."
            )
        return reference


def reference_label(reference: dict | None) -> str:
    if not reference:
        return "No vault reference"
    if reference["provider"] == "onepassword":
        return f"op://{reference['vault']}/{reference['item']}"
    return f"Bitwarden {reference['vault_server']} · {reference['item']}"


@contextmanager
def credential_session(profile: Profile, secret: dict[str, str]):
    """One temporary CLI profile, private files, and child-only passphrase environment."""
    with tempfile.TemporaryDirectory(prefix="snowbeam-session-") as directory:
        root = Path(directory)
        settings = dict(profile.settings)
        for key in ("token_file_path", "private_key_file", "private_key_path"):
            settings.pop(key, None)
        environment = {}
        if set(secret) == {"token"}:
            path = root / "credential.token"
            atomic_write(path, secret["token"])
            settings.update(authenticator="PROGRAMMATIC_ACCESS_TOKEN", token_file_path=str(path))
        elif set(secret) == {"private_key", "passphrase"}:
            path = root / "credential.p8"
            atomic_write(path, secret["private_key"])
            settings.update(authenticator="SNOWFLAKE_JWT", private_key_file=str(path))
            environment["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"] = secret["passphrase"]
        else:
            raise VaultError("The vault item contains an unsupported credential payload.")
        config_path = root / "config.toml"
        atomic_write(
            config_path,
            tomlkit.dumps({"default_connection_name": profile.name, "logs": {"save_logs": False}}),
        )
        source = root / "connections.toml"
        atomic_write(source, tomlkit.dumps({profile.name: settings}))
        # Remove inherited Snowflake credential/config overrides so the child cannot
        # silently use the consultant's/admin's identity instead of this agent.
        child_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("SNOWFLAKE_", "SNOWSQL_", "BW_", "OP_"))
        }
        child_env.update(environment)
        child_env["SNOWFLAKE_HOME"] = directory
        yield Profile(profile.name, config_path, source, settings, environment=child_env)


@contextmanager
def workload_session(profile: Profile):
    """Let the installed Snowflake driver obtain the runtime's short-lived identity."""
    with tempfile.TemporaryDirectory(prefix="snowbeam-workload-") as directory:
        path = Path(directory) / "config.toml"
        source = path.with_name("connections.toml")
        atomic_write(
            path,
            tomlkit.dumps({"default_connection_name": profile.name, "logs": {"save_logs": False}}),
        )
        atomic_write(source, tomlkit.dumps({profile.name: profile.settings}))
        environment = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("SNOWFLAKE_", "SNOWSQL_", "BW_", "OP_"))
        }
        environment["SNOWFLAKE_HOME"] = directory
        yield Profile(profile.name, path, source, profile.settings, environment=environment)
