import base64
import json
import os
import subprocess
from pathlib import Path

import pytest

from snowbeam.config import Profile
from snowbeam.vault import Vault, VaultError, credential_session, workload_session

ID = "00000000-0000-0000-0000-000000000001"
OP = {"provider": "onepassword", "vault": "vault-id", "vault_account": "account-id", "item": ID}
BW = {
    "provider": "bitwarden",
    "vault_server": "https://vault.bitwarden.eu",
    "vault_user": ID,
    "item": ID,
}


@pytest.mark.parametrize("scope", [OP, BW])
def test_vault_create_uses_stdin_and_verifies_read_without_secret_argv(scope, monkeypatch):
    calls = []
    stored = {}
    monkeypatch.setattr("shutil.which", lambda tool: "/bin/" + tool)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert "ULTRA_SECRET_PAT" not in str(command)
        if "status" in command:
            output = {"status": "unlocked", "serverUrl": BW["vault_server"], "userId": ID}
        elif "vault" in command and "get" in command:
            output = {"id": "vault-id"}
        elif "create" in command:
            payload = kwargs["input"]
            stored.update(
                json.loads(
                    base64.b64decode(payload) if scope["provider"] == "bitwarden" else payload
                )
            )
            output = {**stored, "id": ID}
        else:
            output = {**stored, "id": ID}
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    monkeypatch.setattr(subprocess, "run", run)
    result = Vault(scope).create("SAM", {"token": "ULTRA_SECRET_PAT"})
    assert result["item"] == ID
    assert result["credential_kind"] == "PAT"
    assert "ULTRA_SECRET_PAT" not in json.dumps(result)
    assert any("get" in c[0] and "item" in c[0] for c in calls)
    if scope["provider"] == "bitwarden":
        assert stored["organizationId"] is None


def test_bitwarden_wrong_server_or_user_is_rejected_before_read(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool: tool)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {"status": "unlocked", "serverUrl": "https://vault.bitwarden.com", "userId": ID}
            ),
            "",
        ),
    )
    with pytest.raises(VaultError, match="exact server"):
        Vault(BW).read()


def test_vault_existing_login_fields_and_ambiguity(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool: tool)
    bw = Vault(BW)
    assert bw._fields(
        {"login": {"password": "SECRET"}}, {"credential_kind": "PAT", "token_field": "password"}
    ) == {"token": "SECRET"}
    op = Vault(OP)
    assert op._fields(
        {"fields": [{"id": "password", "type": "CONCEALED", "value": "SECRET"}]},
        {"credential_kind": "PAT", "token_field": "password"},
    ) == {"token": "SECRET"}
    with pytest.raises(VaultError, match="ambiguous"):
        op._fields(
            {"fields": [{"label": "token", "value": "one"}, {"label": "token", "value": "two"}]}, {}
        )
    assert op._fields(
        {
            "fields": [
                {"id": "key", "label": "Private key", "value": "KEY", "section": {"id": "ssh"}}
            ]
        },
        {"credential_kind": "KEYPAIR", "key_field": "ssh/key"},
    ) == {"private_key": "KEY", "passphrase": ""}


def test_vault_errors_never_echo_provider_output(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool: tool)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "SECRET_TOKEN_VALUE", "PRIVATE_KEY_VALUE"
        ),
    )
    with pytest.raises(VaultError) as error:
        Vault(OP).read()
    assert "SECRET" not in str(error.value)
    assert "PRIVATE_KEY" not in str(error.value)


def test_credential_session_contains_only_target_and_cleans_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "ADMIN_SECRET")
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_SAM_USER", "ACCOUNTADMIN_USER")
    monkeypatch.setenv("BW_SESSION", "VAULT_MASTER")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "VAULT_MASTER")
    profile = Profile(
        "sam",
        tmp_path / "config.toml",
        tmp_path / "config.toml",
        {"account": "ACME-PROD", "user": "SAM", "role": "ANALYST_AGENT"},
    )
    with credential_session(profile, {"token": "PAT_SECRET"}) as actual:
        root = actual.config_path.parent
        path = Path(actual.settings["token_file_path"])
        assert path.read_text() == "PAT_SECRET"
        assert path.stat().st_mode & 0o777 == 0o600
        assert "PAT_SECRET" not in actual.config_path.read_text()
        assert "PAT_SECRET" not in actual.source_path.read_text()
        assert "ADMIN_SECRET" not in json.dumps(actual.environment)
        assert "VAULT_MASTER" not in json.dumps(actual.environment)
        assert "SNOWFLAKE_CONNECTIONS_SAM_USER" not in actual.environment
        assert os.environ["SNOWFLAKE_PASSWORD"] == "ADMIN_SECRET"
    assert not root.exists()


def test_workload_session_preserves_runtime_identity_but_not_other_snowflake_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/sam")
    monkeypatch.setenv("SNOWFLAKE_TOKEN", "ADMIN")
    profile = Profile(
        "sam",
        tmp_path / "c",
        tmp_path / "c",
        {
            "account": "ACME-PROD",
            "user": "SAM",
            "authenticator": "WORKLOAD_IDENTITY",
            "workload_identity_provider": "AWS",
        },
    )
    with workload_session(profile) as actual:
        assert actual.environment["AWS_ROLE_ARN"].endswith("role/sam")
        assert "SNOWFLAKE_TOKEN" not in actual.environment
        assert "workload_identity_provider" in actual.source_path.read_text()


def test_real_openssl_generates_encrypted_3072_bit_key_and_matching_fingerprint():
    import hashlib
    import shutil

    from snowbeam.provision import generate_key

    if not shutil.which("openssl"):
        pytest.skip("OpenSSL not installed")
    secret, public, fingerprint = generate_key()
    assert secret["private_key"].startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----")
    assert len(secret["passphrase"]) >= 64
    environment = dict(os.environ, SNOWBEAM_TEST_PASSPHRASE=secret["passphrase"])
    derived = subprocess.run(
        [
            "openssl",
            "pkey",
            "-pubout",
            "-outform",
            "DER",
            "-passin",
            "env:SNOWBEAM_TEST_PASSPHRASE",
        ],
        input=secret["private_key"].encode(),
        capture_output=True,
        env=environment,
        check=True,
    ).stdout
    assert derived == base64.b64decode(public)
    assert fingerprint == "SHA256:" + base64.b64encode(hashlib.sha256(derived).digest()).decode()
    description = subprocess.run(
        ["openssl", "pkey", "-pubin", "-inform", "DER", "-text", "-noout"],
        input=derived,
        capture_output=True,
        check=True,
    ).stdout
    assert b"3072 bit" in description


@pytest.mark.parametrize("fields", ["secret", [{"id": []}], [{"section": "secret"}]])
def test_malformed_vault_metadata_has_fixed_errors(monkeypatch, fields):
    monkeypatch.setattr("shutil.which", lambda tool: tool)
    with pytest.raises(VaultError) as error:
        Vault(OP)._fields({"fields": fields}, {"credential_kind": "PAT"})
    assert "secret" not in str(error.value)
