import stat

import pytest
import tomlkit

from snowdock.config import Config, ConfigError, default_config_path


def test_connections_toml_precedence_and_preserve_secrets_comments(tmp_path):
    main = tmp_path / "config.toml"
    main.write_text(
        'default_connection_name = "work"\n[connections.ignored]\naccount="OLD"\nuser="OLD"\n'
    )
    shared = tmp_path / "connections.toml"
    shared.write_text(
        '# Keep this comment\n[work]\naccount="ACME-PROD"\nuser="ALICE"\n'
        'password="test-secret-do-not-cache"\ncustom_option=true\n'
    )
    config = Config(main)
    profiles = config.profiles()
    assert [p.name for p in profiles] == ["work"]
    assert profiles[0].is_default
    assert "password" not in profiles[0].settings
    config.save("work", {"role": "ANALYST"})
    text = shared.read_text()
    assert "# Keep this comment" in text
    values = tomlkit.parse(text)["work"]
    assert values["password"] == "test-secret-do-not-cache"
    assert values["custom_option"] is True
    assert values["role"] == "ANALYST"
    assert "OLD" in main.read_text()
    assert stat.S_IMODE(shared.stat().st_mode) == 0o600
    assert stat.S_IMODE(shared.with_name("connections.toml.snowdock.bak").stat().st_mode) == 0o600


def test_unknown_fields_cannot_be_added_to_metadata_config(service):
    with pytest.raises(ConfigError, match="Only connection settings"):
        service.config.save("work", {"password": "new-secret"})


def test_environment_is_visible_but_not_written_back(service, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_CONNECTIONS_WORK_ROLE", "OVERRIDE_ROLE")
    assert service.config.profile("work").settings["role"] == "OVERRIDE_ROLE"
    assert "role" not in service.config.profile("work", environment=False).settings
    assert "OVERRIDE_ROLE" not in service.config.source.read_text()


def test_default_and_remove_use_shared_file(tmp_path):
    main = tmp_path / "config.toml"
    main.write_text("")
    (tmp_path / "connections.toml").write_text(
        '[one]\naccount="A"\nuser="U"\n[two]\naccount="B"\nuser="U"\n'
    )
    config = Config(main)
    config.set_default("one")
    assert config.profile("one").is_default
    config.remove("one")
    assert [p.name for p in config.profiles()] == ["two"]
    assert "default_connection_name" not in main.read_text()


def test_invalid_toml_is_not_treated_as_empty(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('password="SECRET\nBROKEN')
    with pytest.raises(ConfigError) as error:
        Config(path).profiles()
    assert "SECRET" not in str(error.value)


def test_concurrent_external_edit_is_not_overwritten(service):
    path = service.config.path
    with pytest.raises(ConfigError, match="another program"):
        with service.config._edit(path) as document:
            document["another"] = "value"
            path.write_text("# External change\n")
    assert path.read_text() == "# External change\n"


def test_symlink_configuration_cannot_be_replaced(tmp_path):
    target = tmp_path / "real.toml"
    target.write_text('[connections.work]\naccount="A"\nuser="U"\n')
    link = tmp_path / "config.toml"
    link.symlink_to(target)
    with pytest.raises(ConfigError, match="symlink"):
        Config(link).save("work", {"role": "R"})
    assert link.is_symlink()
    assert "role" not in target.read_text()


def test_discovery_honors_snowflake_home(monkeypatch, tmp_path):
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    assert default_config_path() == tmp_path / "config.toml"


def test_update_preserves_unedited_connection(service):
    service.config.save("two", {"account": "B", "user": "OTHER"}, create=True)
    service.config.save("work", {"role": "R"})
    assert service.config.profile("two").settings["user"] == "OTHER"
    with pytest.raises(ConfigError, match="already exists"):
        service.config.save("two", {"account": "B", "user": "U"}, create=True)
    with pytest.raises(ConfigError, match="required"):
        service.config.save("work", {"user": ""})
    assert service.config.profile("work").settings["user"] == "ALICE"
