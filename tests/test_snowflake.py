import json
import subprocess

import pytest
from conftest import IDENTITY

from snowbeam.config import Profile
from snowbeam.snowflake import (
    IDENTITY_SQL,
    SnowClient,
    SnowError,
    parse_rows,
    safe_error,
    safe_text,
)


def test_command_uses_argv_and_contains_no_secrets(service, monkeypatch):
    captured = []

    def run(command, **kwargs):
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps([IDENTITY]), "")

    monkeypatch.setattr(subprocess, "run", run)
    profile = service.config.profile("work")
    # Even a hostile imported profile name is data, not shell syntax or SQL.
    hostile = Profile(
        "profile; touch /tmp/never-created",
        profile.config_path,
        profile.source_path,
        profile.settings,
    )
    result = SnowClient("snow").identity(hostile)
    assert result["organization_name"] == "ACME"
    command, options = captured[0]
    assert command[command.index("--connection") + 1] == hostile.name
    assert command[command.index("--query") + 1] == IDENTITY_SQL
    assert not options.get("shell")
    assert options["stdin"] == subprocess.DEVNULL


def test_cli_error_does_not_expose_token(service, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, "", "Authentication failed for password=SUPER-SECRET"
        ),
    )
    with pytest.raises(SnowError) as error:
        SnowClient("snow").identity(service.config.profile("work"))
    assert error.value.code == "authentication"
    assert "SUPER-SECRET" not in str(error.value)


def test_timeout_and_malformed_output_are_explicit(service, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(SnowError, match="did not respond"):
        SnowClient("snow").identity(service.config.profile("work"))
    with pytest.raises(SnowError, match="JSON"):
        parse_rows("SECRET invalid output")
    with pytest.raises(SnowError, match="unexpected"):
        parse_rows('{"not": "rows"}')


def test_uppercase_json_columns_are_normalized():
    assert parse_rows('[{"ORGANIZATION_NAME":"ACME"}]') == [{"organization_name": "ACME"}]


def test_disabled_metadata_is_not_inferred_from_failed_authentication():
    assert safe_error("network policy denied the IP address").code == "network_policy"
    assert safe_error("insufficient privileges").code == "permission"
    assert safe_error("token has expired").code == "expired"
    assert "SECRET" not in str(safe_error("Unknown error, token=SECRET"))


def test_terminal_control_sequences_are_removed():
    assert safe_text("a\x1b[31mb\nc\x07") == "abc"


def test_noninteractive_browser_profile_never_starts_subprocess(service, monkeypatch):
    service.config.save("work", {"authenticator": "externalbrowser"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Unexpected process"))
    with pytest.raises(SnowError) as error:
        SnowClient("snow").identity(service.config.profile("work"), interactive=False)
    assert error.value.code == "sign_in"


def test_snapshot_binds_identity_and_tokens_to_one_session(service, monkeypatch):
    from conftest import TOKEN

    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["env"]["SNOWFLAKE_HOME"] == str(service.config.path.parent)
        return subprocess.CompletedProcess(command, 0, json.dumps([[IDENTITY], [TOKEN]]), "")

    monkeypatch.setattr(subprocess, "run", run)
    identity, tokens = SnowClient("snow").snapshot(service.config.profile("work"))
    assert identity == IDENTITY
    assert tokens == [TOKEN]
    assert len(commands) == 1


def test_snapshot_rejects_wrong_principal_and_supports_empty_inventory(service, monkeypatch):
    from conftest import TOKEN

    payload = [[IDENTITY], [dict(TOKEN, user_name="SOMEONE_ELSE")]]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(payload), ""),
    )
    with pytest.raises(SnowError, match="authenticated user"):
        SnowClient("snow").snapshot(service.config.profile("work"))
    payload[1] = []
    assert SnowClient("snow").snapshot(service.config.profile("work"))[1] == []
