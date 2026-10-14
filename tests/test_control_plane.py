import os
import time

import pytest

from snowbeam.admin_challenge import (
    AdminAccessDenied,
    AdminLockoutStore,
    apply_decoy_to_child_env,
    decoy_passphrase_for_connection,
    unlock_admin_passphrase,
)
from snowbeam.agent_registry import AgentRegistry
from snowbeam.connection_access import CLASS_ADMIN, CLASS_AGENT, ConnectionAccess
from snowbeam.snow_runner import child_environment, parse_snow_connection, run_snow
from snowbeam.snow_views import materialize_view


@pytest.fixture
def snow_home(tmp_path, monkeypatch):
    home = tmp_path / "snowflake"
    home.mkdir()
    main = home / "config.toml"
    main.write_text('default_connection_name = "work"\n')
    conn = home / "connections.toml"
    key = tmp_path / "keys"
    key.mkdir()
    admin_key = key / "work.p8"
    admin_key.write_text("-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n")
    agent_key = key / "bot.p8"
    agent_key.write_text("-----BEGIN PRIVATE KEY-----\nMIIC\n-----END PRIVATE KEY-----\n")
    conn.write_text(
        f'[work]\naccount="ACME"\nuser="ALICE"\n'
        f'authenticator="SNOWFLAKE_JWT"\nprivate_key_file="{admin_key}"\n\n'
        f'[bot]\naccount="ACME"\nuser="BOT"\n'
        f'authenticator="SNOWFLAKE_JWT"\nprivate_key_file="{agent_key}"\n'
    )
    monkeypatch.setenv("SNOWFLAKE_HOME", str(home))
    from snowbeam.config import Config

    return Config(main), tmp_path / "state"


@pytest.fixture
def classified(snow_home):
    config, state = snow_home
    access = ConnectionAccess(state)
    access.set_classification("work", CLASS_ADMIN)
    access.set_classification("bot", CLASS_AGENT)
    return config, state, access


def test_unclassified_defaults_admin(snow_home):
    _, state = snow_home
    access = ConnectionAccess(state)
    assert access.classification("work") == CLASS_ADMIN
    assert access.classification("missing") == CLASS_ADMIN


def test_agent_visibility(classified):
    config, state, access = classified
    names = [p.name for p in config.profiles(environment=False)]
    assert access.visible_connection_names(names, agent_context=True) == ["bot"]
    assert set(access.visible_connection_names(names, agent_context=False)) == {"work", "bot"}


def test_snow_views_filter(classified):
    config, state, access = classified
    admin_home = materialize_view(config, access, state, group=CLASS_ADMIN)
    agent_home = materialize_view(config, access, state, group=CLASS_AGENT)
    admin_text = (admin_home / "connections.toml").read_text()
    agent_text = (agent_home / "connections.toml").read_text()
    assert "work" in admin_text and "bot" in admin_text
    assert "bot" in agent_text and "work" not in agent_text


def test_decoy_env():
    env = {}
    apply_decoy_to_child_env(env, "work")
    assert env["PRIVATE_KEY_PASSPHRASE"].startswith("decoy-")
    assert env["PRIVATE_KEY_PASSPHRASE"] != decoy_passphrase_for_connection("other")


def test_lockout_escalates(classified):
    _, state, _ = classified
    lockout = AdminLockoutStore(state)
    now = time.time()
    lockout.record_failure(now)
    assert not lockout.is_locked(now)
    state2 = lockout.record_failure(now)
    assert state2.locked_until is not None
    assert lockout.is_locked(now)


def test_unlock_denies_without_tty(classified):
    _, state, _ = classified

    class Store:
        def get_passphrase(self, connection):
            return "secret"

    lockout = AdminLockoutStore(state)
    with pytest.raises(AdminAccessDenied):
        unlock_admin_passphrase(
            "work",
            Store(),
            lockout,
            silent_on_denial=True,
            tty_getpass_fn=lambda prompt: "secret",
        )


def test_unlock_checks_passphrase(classified, monkeypatch):
    _, state, _ = classified

    class Store:
        def get_passphrase(self, connection):
            return "secret"

    lockout = AdminLockoutStore(state)
    monkeypatch.setattr("snowbeam.admin_challenge.stdin_is_interactive_tty", lambda: True)
    with pytest.raises(AdminAccessDenied):
        unlock_admin_passphrase(
            "work",
            Store(),
            lockout,
            silent_on_denial=False,
            tty_getpass_fn=lambda prompt: "wrong",
        )
    value = unlock_admin_passphrase(
        "work",
        Store(),
        lockout,
        silent_on_denial=False,
        tty_getpass_fn=lambda prompt: "secret",
    )
    assert value == "secret"


def test_agent_proof_isolated(classified):
    _, state, _ = classified
    registry = AgentRegistry(state)
    registry.confirm_register("a", approve_digest=registry.plan_register("a")["digest"])
    registry.confirm_register("b", approve_digest=registry.plan_register("b")["digest"])
    proof_a = registry.mint_proof("a")
    assert registry.verify_proof("a", proof_a)
    assert not registry.verify_proof("b", proof_a)


def test_run_snow_agent_cannot_use_admin(classified, monkeypatch):
    config, state, _ = classified
    registry = AgentRegistry(state)
    registry.confirm_register("sam", approve_digest=registry.plan_register("sam")["digest"])
    proof = registry.mint_proof("sam")

    captured = {}

    class FakeProcess:
        def wait(self):
            return 0

    def fake_popen(cmd, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/snow" if name == "snow" else None)
    code = run_snow(
        ["connection", "test", "-c", "work"],
        config=config,
        state_dir=state,
        executable="/usr/bin/snow",
        popen=fake_popen,
        agent_id="sam",
        agent_proof=proof,
        environ={},
    )
    assert code == AdminAccessDenied.exit_code
    assert "cmd" not in captured


def test_run_snow_agent_uses_agent_view(classified, monkeypatch):
    config, state, _ = classified
    registry = AgentRegistry(state)
    digest = registry.plan_register("sam")["digest"]
    registry.confirm_register("sam", approve_digest=digest)
    proof = registry.mint_proof("sam")
    captured = {}

    class FakeProcess:
        def wait(self):
            return 3

    class Store:
        def get_passphrase(self, connection):
            assert connection == "bot"
            return "bot-secret"

    def fake_popen(cmd, env=None):
        captured["env"] = env
        return FakeProcess()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/snow" if name == "snow" else None)
    code = run_snow(
        ["connection", "test", "-c", "bot"],
        config=config,
        state_dir=state,
        store=Store(),
        executable="/usr/bin/snow",
        popen=fake_popen,
        agent_id="sam",
        agent_proof=proof,
        environ={},
    )
    assert code == 3
    assert captured["env"]["SNOWFLAKE_HOME"].endswith("/snow-views/agent")
    assert captured["env"]["PRIVATE_KEY_PASSPHRASE"] == "bot-secret"


def test_child_env_sets_snowflake_home():
    env = child_environment(os.environ, passphrase=None, snowflake_home="/tmp/view")
    assert env["SNOWFLAKE_HOME"] == "/tmp/view"


def test_parse_connection_unchanged(snow_home):
    config, _ = snow_home
    assert parse_snow_connection(["-c", "bot", "sql"], config) == "bot"
