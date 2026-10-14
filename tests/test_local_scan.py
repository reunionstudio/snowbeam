import os

import pytest

from snowbeam.config import Config
from snowbeam.local_scan import scan_exit_code, scan_local_machine
from snowbeam.os_secrets import keychain_service
from snowbeam.snow_runner import (
    child_environment,
    parse_snow_connection,
    render_shell_wrapper,
    run_snow,
)


@pytest.fixture
def snow_config(tmp_path, monkeypatch):
    home = tmp_path / "snowflake"
    home.mkdir()
    main = home / "config.toml"
    main.write_text('default_connection_name = "work"\n')
    conn = home / "connections.toml"
    key = tmp_path / "keys"
    key.mkdir()
    key_file = key / "work.p8"
    key_file.write_text("-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n")
    conn.write_text(
        f'[work]\naccount="ACME"\nuser="ALICE"\n'
        f'authenticator="SNOWFLAKE_JWT"\nprivate_key_file="{key_file}"\n'
    )
    monkeypatch.setenv("SNOWFLAKE_HOME", str(home))
    return Config(main)


def test_scan_finds_plaintext_password_and_unencrypted_key(snow_config, tmp_path):
    shared = snow_config.source
    shared.write_text(
        shared.read_text()
        + f'\n[other]\naccount="X"\nuser="Y"\npassword="secret"\n'
        f'private_key_file="{snow_config.profile("work").settings["private_key_file"]}"\n'
    )
    report = scan_local_machine(snow_config)
    checks = {row["check"] for row in report["findings"]}
    assert "plaintext_secret_field" in checks
    assert "unencrypted_private_key" in checks
    assert "shared_key_file" in checks
    assert scan_exit_code(report) == 2
    assert not any("password" in row["message"] and "secret" in row["message"] for row in report["findings"])


def test_scan_finds_env_and_shell_export(snow_config, tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_KEY_PASSPHRASE", "inherited")
    rc = tmp_path / ".zshrc"
    rc.write_text('export PRIVATE_KEY_PASSPHRASE="from-profile"\n')
    monkeypatch.setattr("snowbeam.local_scan.SHELL_STARTUP_FILES", (rc,))
    report = scan_local_machine(snow_config)
    assert any(row["check"] == "environment_secret" for row in report["findings"])
    assert any(row["check"] == "shell_export" for row in report["findings"])


def test_scan_permissions(snow_config):
    os.chmod(snow_config.source, 0o644)
    report = scan_local_machine(snow_config)
    assert any(row["check"] == "permissions" for row in report["findings"])


def test_parse_snow_connection_default_and_flag(snow_config):
    assert parse_snow_connection(["connection", "test"], snow_config) == "work"
    assert parse_snow_connection(["-c", "other", "sql"], snow_config) == "other"


def test_child_env_strips_parent_secrets(monkeypatch):
    monkeypatch.setenv("PRIVATE_KEY_PASSPHRASE", "parent")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "parent")
    monkeypatch.setenv("HOME", "/tmp")
    child = child_environment(os.environ, passphrase="child-only")
    assert child["PRIVATE_KEY_PASSPHRASE"] == "child-only"
    assert "SNOWFLAKE_PASSWORD" not in child
    assert os.environ["PRIVATE_KEY_PASSPHRASE"] == "parent"


def test_run_snow_passes_exit_code_and_child_env(snow_config, monkeypatch):
    captured = {}

    class FakeProcess:
        def wait(self):
            return 7

    def fake_popen(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return FakeProcess()

    class Store:
        def get_passphrase(self, connection):
            assert connection == "work"
            return "keychain-secret"

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/snow" if name == "snow" else None)
    code = run_snow(
        ["connection", "test", "-c", "work"],
        config=snow_config,
        store=Store(),
        executable="/usr/bin/snow",
        popen=fake_popen,
    )
    assert code == 7
    assert captured["env"]["PRIVATE_KEY_PASSPHRASE"] == "keychain-secret"
    assert "SNOWFLAKE_PASSWORD" not in captured["env"]
    assert os.environ.get("PRIVATE_KEY_PASSPHRASE") != "keychain-secret"


def test_wrapper_references_snowbeam_snow():
    text = render_shell_wrapper()
    assert "snowbeam snow" in text


def test_keychain_service_naming():
    assert keychain_service("work") == "snowbeam.work"
