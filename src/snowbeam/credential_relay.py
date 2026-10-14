"""One-time human handoff for managed credentials. No vault and no persistence of secrets."""

from __future__ import annotations

from pathlib import Path

from .config import Profile, atomic_write
from .vault import credential_session


def vault_integration_enabled(document: dict) -> bool:
    return bool((document.get("preferences") or {}).get("vault_integration"))


def build_relay(spec: dict, secret: dict[str, str], *, operation_id: str, token_name: str | None):
    account = f"{spec['organization']}-{spec['account']}"
    title = f"Snowbeam · {account} · {spec['user']} · {operation_id}"
    fields = {
        "account": account,
        "user": spec["user"],
        "role": spec["role"],
        "authenticator": spec["auth"],
    }
    if spec["auth"] == "PAT":
        fields["token_name"] = token_name or ""
        fields["token"] = secret["token"]
        instructions = (
            "Save the programmatic access token in your password manager. Snowbeam does not "
            "store it."
        )
    else:
        fields["private_key"] = secret["private_key"]
        fields["passphrase"] = secret["passphrase"]
        instructions = (
            "Save the encrypted private key and passphrase in your password manager. Snowbeam "
            "does not store them."
        )
    return {
        "suggested_item_title": title,
        "fields": fields,
        "instructions": instructions,
        "confirm_with": "snowbeam identities confirm-relay IDENTITY",
    }


def load_secret_from_files(
    spec: dict, *, token_file: Path | None, private_key_file: Path | None, passphrase: str | None
) -> dict[str, str]:
    if spec["auth"] == "PAT":
        if not token_file:
            raise ValueError("Supply --token-file with the saved programmatic access token.")
        value = token_file.expanduser().read_text().strip()
        if not value:
            raise ValueError("The token file is empty.")
        return {"token": value}
    if not private_key_file:
        raise ValueError("Supply --private-key-file with the saved private key.")
    key = private_key_file.expanduser().read_text()
    if not key.strip():
        raise ValueError("The private key file is empty.")
    return {"private_key": key, "passphrase": passphrase or ""}


def verification_session(
    profile: Profile,
    spec: dict,
    *,
    token_file: Path | None = None,
    private_key_file: Path | None = None,
    passphrase: str | None = None,
    reference: dict | None = None,
    vault_integration: bool = False,
):

    if spec["auth"] == "WIF":
        from .vault import workload_session

        return workload_session(profile)
    if vault_integration and reference and not (token_file or private_key_file):
        from .vault import Vault

        return credential_session(profile, Vault(reference).read())
    secret = load_secret_from_files(
        spec,
        token_file=token_file,
        private_key_file=private_key_file,
        passphrase=passphrase,
    )
    return credential_session(profile, secret)


def write_ephemeral_token(path: Path, token: str) -> Path:
    atomic_write(path, token)
    return path
