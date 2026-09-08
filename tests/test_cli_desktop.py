import json
import plistlib
import subprocess
import sys
from datetime import timedelta

from conftest import IDENTITY, TOKEN

from snowdock.cli import main
from snowdock.desktop import install_launcher, install_reminders, notify_due, quoted_exec
from snowdock.store import iso, utcnow


def test_demo_is_isolated_and_exits_with_alert_status(tmp_path, monkeypatch, capsys):
    user_config = tmp_path / "config.toml"
    user_config.write_text("# Deliberately broken TOML [SECRET")
    monkeypatch.setenv("SNOWFLAKE_HOME", str(tmp_path))
    assert main(["--demo", "check", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["alerts"]) == 2
    assert result["verification_issues"] == []
    assert user_config.read_text() == "# Deliberately broken TOML [SECRET"


def test_cached_check_reports_unverified_not_all_clear(service, capsys):
    args = ["--snow-config", str(service.config.path), "--state-dir", str(service.store.directory)]
    assert main(args + ["check", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["alerts"] == []
    assert result["verification_issues"]


def test_offline_cli_add_edit_default_remove_round_trip(tmp_path, capsys):
    args = ["--snow-config", str(tmp_path / "config.toml"), "--state-dir", str(tmp_path / "data")]
    assert (
        main(args + ["connections", "add", "dev", "--account", "ACME-DEV", "--user", "ALICE"]) == 0
    )
    assert main(args + ["connections", "edit", "dev", "--role", "ANALYST"]) == 0
    assert main(args + ["connections", "default", "dev"]) == 0
    capsys.readouterr()
    assert main(args + ["connections", "list", "--json"]) == 0
    row = json.loads(capsys.readouterr().out)[0]
    assert row["settings"]["role"] == "ANALYST"
    assert row["is_default"]
    assert main(args + ["connections", "remove", "dev"]) == 2
    assert main(args + ["connections", "remove", "dev", "--yes"]) == 0


def test_notification_deduplication_and_failed_delivery(service, monkeypatch):
    account = service.store.connected(service.config.profile("work"), IDENTITY)
    service.store.save_tokens(
        account, "ALICE", [dict(TOKEN, expires_at=iso(utcnow() + timedelta(days=2)))]
    )
    alerts = service.store.alerts()
    monkeypatch.setattr("snowdock.desktop.send_notification", lambda *args: False)
    assert notify_due(service.store, alerts, []) == (0, 1)
    assert not service.store.notice_sent(alerts[0]["notice_key"])
    sent = []
    monkeypatch.setattr(
        "snowdock.desktop.send_notification", lambda *args: sent.append(args) or True
    )
    assert notify_due(service.store, alerts, []) == (1, 0)
    assert notify_due(service.store, alerts, []) == (0, 0)
    assert len(sent) == 1


def test_scheduler_files_are_opt_in_and_paths_are_quoted(service, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config space"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data space"))
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command) or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    launcher = install_launcher(service)
    assert "Terminal=true" in launcher.read_text()
    assert "--state-dir" in launcher.read_text()
    paths = install_reminders(service)
    assert '"check" "--refresh" "--notify"' in paths[0].read_text()
    assert "Persistent=true" in paths[1].read_text()
    assert calls[-1] == ["systemctl", "--user", "enable", "--now", "snowdock-reminders.timer"]
    assert quoted_exec(["a b", 'cash$%"'], systemd=True) == '"a b" "cash$$%%\\""'


def test_macos_reminder_passes_paths_as_arguments(service, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", "")
    )
    path = install_reminders(service)[0]
    data = plistlib.loads(path.read_bytes())
    assert data["ProgramArguments"][-3:] == ["check", "--refresh", "--notify"]
    assert str(service.config.path) in data["ProgramArguments"]
    assert data["RunAtLoad"] is True
