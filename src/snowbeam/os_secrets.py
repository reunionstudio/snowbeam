"""OS-backed passphrases for Snowflake CLI connections (not 1Password/Bitwarden)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from abc import ABC, abstractmethod


class SecretStoreError(Exception):
    """Keychain or secret service failure without secret values in the message."""


def keychain_service(connection: str) -> str:
    return f"snowbeam.{connection}"


def keychain_account(connection: str) -> str:
    return connection


class SecretStore(ABC):
    @abstractmethod
    def get_passphrase(self, connection: str) -> str | None: ...

    @abstractmethod
    def set_passphrase(self, connection: str, passphrase: str) -> None: ...

    @abstractmethod
    def delete_passphrase(self, connection: str) -> None: ...

    def status(self, connection: str) -> dict:
        present = self.get_passphrase(connection) is not None
        return {
            "connection": connection,
            "service": keychain_service(connection),
            "account": keychain_account(connection),
            "stored": present,
        }


class MacOSKeychainStore(SecretStore):
    def get_passphrase(self, connection: str) -> str | None:
        executable = shutil.which("security")
        if not executable:
            raise SecretStoreError("macOS Keychain requires the security command.")
        result = subprocess.run(
            [
                executable,
                "find-generic-password",
                "-s",
                keychain_service(connection),
                "-a",
                keychain_account(connection),
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        value = (result.stdout or "").rstrip("\n")
        return value or None

    def set_passphrase(self, connection: str, passphrase: str) -> None:
        executable = shutil.which("security")
        if not executable:
            raise SecretStoreError("macOS Keychain requires the security command.")
        service = keychain_service(connection)
        account = keychain_account(connection)
        delete = subprocess.run(
            [executable, "delete-generic-password", "-s", service, "-a", account],
            capture_output=True,
            text=True,
        )
        if delete.returncode not in {0, 44}:
            raise SecretStoreError("Could not update the Keychain item.")
        add = subprocess.run(
            [
                executable,
                "add-generic-password",
                "-s",
                service,
                "-a",
                account,
                "-w",
                passphrase,
            ],
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            raise SecretStoreError("Could not store the passphrase in Keychain.")

    def delete_passphrase(self, connection: str) -> None:
        executable = shutil.which("security")
        if not executable:
            raise SecretStoreError("macOS Keychain requires the security command.")
        result = subprocess.run(
            [
                executable,
                "delete-generic-password",
                "-s",
                keychain_service(connection),
                "-a",
                keychain_account(connection),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode not in {0, 44}:
            raise SecretStoreError("Could not remove the Keychain item.")


class SecretToolStore(SecretStore):
    """Optional Linux secret-service backend via secret-tool."""

    def get_passphrase(self, connection: str) -> str | None:
        executable = shutil.which("secret-tool")
        if not executable:
            raise SecretStoreError("Install secret-tool to use the Linux secret service.")
        result = subprocess.run(
            [
                executable,
                "lookup",
                "service",
                "snowbeam",
                "connection",
                connection,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        value = (result.stdout or "").rstrip("\n")
        return value or None

    def set_passphrase(self, connection: str, passphrase: str) -> None:
        executable = shutil.which("secret-tool")
        if not executable:
            raise SecretStoreError("Install secret-tool to use the Linux secret service.")
        result = subprocess.run(
            [
                executable,
                "store",
                "--label",
                f"Snowbeam passphrase for Snowflake connection {connection}",
                "service",
                "snowbeam",
                "connection",
                connection,
            ],
            input=passphrase,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise SecretStoreError("Could not store the passphrase in the secret service.")

    def delete_passphrase(self, connection: str) -> None:
        executable = shutil.which("secret-tool")
        if not executable:
            raise SecretStoreError("Install secret-tool to use the Linux secret service.")
        value = self.get_passphrase(connection)
        if value is None:
            return
        subprocess.run(
            [
                executable,
                "clear",
                "service",
                "snowbeam",
                "connection",
                connection,
            ],
            capture_output=True,
            text=True,
        )


class UnavailableSecretStore(SecretStore):
    def get_passphrase(self, connection: str) -> str | None:
        return None

    def set_passphrase(self, connection: str, passphrase: str) -> None:
        raise SecretStoreError(
            "No OS secret store is available on this platform. Use macOS Keychain or "
            "install secret-tool on Linux."
        )

    def delete_passphrase(self, connection: str) -> None:
        raise SecretStoreError("No OS secret store is available on this platform.")


def default_secret_store() -> SecretStore:
    if sys.platform == "darwin":
        return MacOSKeychainStore()
    if shutil.which("secret-tool"):
        return SecretToolStore()
    return UnavailableSecretStore()


def keychain_add_command(connection: str, *, snowbeam_executable: str = "snowbeam") -> str:
    """One-time command for the human; prompts for the passphrase (never pass -w VALUE)."""
    service = keychain_service(connection)
    account = keychain_account(connection)
    return (
        f'security add-generic-password -s "{service}" -a "{account}" '
        f'-T "{snowbeam_executable}" -T "$(which snow)"'
    )
