import json
import os
import sys
from contextlib import suppress
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from textual.widgets import Button, Static
from textual.worker import WorkerCancelled

from snowbeam import upgrader
from snowbeam.cli import parser
from snowbeam.store import iso
from snowbeam.tui import Snowbeam
from snowbeam.updates import Updates


def package(version="9.0", **values):
    return json.dumps(
        {
            "formulae": [
                {
                    "full_name": upgrader.FORMULA,
                    "tap": "reunionstudio/tap",
                    "versions": {"stable": version},
                    **values,
                }
            ]
        }
    ).encode()


def test_only_an_official_homebrew_installation_can_offer_update(tmp_path, monkeypatch):
    prefix = tmp_path / "Cellar/snowbeam/0.2.0a1/libexec"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    for path in (tmp_path / "opt/snowbeam/bin/snowbeam", tmp_path / "bin/brew"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o700)
    receipt = prefix.parent / "INSTALL_RECEIPT.json"
    assert upgrader.homebrew_upgrader() is None
    receipt.write_text(json.dumps({"source": {"tap": "someone/else"}}))
    assert upgrader.homebrew_upgrader() is None
    receipt.write_text(json.dumps({"source": {"tap": "reunionstudio/tap"}}))
    installer = upgrader.homebrew_upgrader()
    assert installer.brew == tmp_path / "bin/brew"
    assert installer.command == tmp_path / "opt/snowbeam/bin/snowbeam"


def test_upgrade_uses_fixed_commands_and_verifies_new_version(monkeypatch):
    calls = []
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "secret")
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "secret")
    monkeypatch.setenv("BW_SESSION", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    def run(command, environment, **kwargs):
        calls.append(command)
        assert not any(value == "secret" for value in environment.values())
        assert environment["HOMEBREW_NO_INSTALL_CLEANUP"] == "1"
        if "info" in command:
            return package()
        return b"Snowbeam 9.0\n" if "--version" in command else b""

    monkeypatch.setattr(upgrader, "run_installer", run)
    installer = upgrader.HomebrewUpgrade(
        Path("/brew/bin/brew"), Path("/brew/opt/snowbeam/bin/snowbeam")
    )
    progress = []
    assert installer.install("9.0", progress.append) == upgrader.Restart(installer.command)
    assert calls == [
        ["/brew/bin/brew", "update"],
        ["/brew/bin/brew", "info", "--json=v2", "--formula", upgrader.FORMULA],
        ["/brew/bin/brew", "upgrade", "--formula", upgrader.FORMULA],
        ["/brew/opt/snowbeam/bin/snowbeam", "--version"],
    ]
    assert progress[-1] == "Update installed. Restarting Snowbeam…"


@pytest.mark.parametrize(
    "metadata",
    [
        package("0.2.0a1"),
        package(pinned=True),
        package(disabled=True),
        package(full_name="other/tap/snowbeam"),
        b"raw-secret-error",
    ],
)
def test_unavailable_pinned_or_untrusted_packages_never_upgrade(metadata, monkeypatch):
    calls = []

    def run(command, *args, **kwargs):
        calls.append(command)
        return metadata

    monkeypatch.setattr(upgrader, "run_installer", run)
    installer = upgrader.HomebrewUpgrade(Path("/brew"), Path("/snowbeam"))
    with pytest.raises(upgrader.UpgradeError) as error:
        installer.install("9.0", lambda value: None)
    assert not any("upgrade" in command for command in calls)
    assert "raw-secret-error" not in str(error.value)


def test_installer_success_without_new_version_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(
        upgrader,
        "run_installer",
        lambda command, *args, **kwargs: package() if "info" in command else b"Snowbeam 0.2.0a1",
    )
    with pytest.raises(upgrader.UpgradeError, match="could not be verified"):
        upgrader.HomebrewUpgrade(Path("/brew"), Path("/snowbeam")).install("9.0", lambda _: None)


def test_stable_install_does_not_switch_to_a_prerelease(monkeypatch):
    commands = []
    monkeypatch.setattr(upgrader, "__version__", "0.2.0")

    def run(command, *args, **kwargs):
        commands.append(command)
        return package("9.0a1")

    monkeypatch.setattr(upgrader, "run_installer", run)
    with pytest.raises(upgrader.UpgradeError, match="Stable updates only"):
        upgrader.HomebrewUpgrade(Path("/brew"), Path("/snowbeam")).install("9.0a1", lambda _: None)
    assert not any("upgrade" in command for command in commands)


def test_real_subprocess_failure_and_timeout_are_sanitized():
    environment = upgrader.installer_environment()
    assert (
        upgrader.run_installer(
            [sys.executable, "-c", "print('ok')"], environment, timeout=10, capture=True
        )
        == b"ok\n"
    )
    with pytest.raises(upgrader.UpgradeError) as error:
        upgrader.run_installer(
            [sys.executable, "-c", "import sys; print('DO-NOT-LOG'); sys.exit(1)"],
            environment,
            timeout=10,
            capture=True,
        )
    assert "DO-NOT-LOG" not in str(error.value)
    with pytest.raises(upgrader.UpgradeError, match="timed out"):
        upgrader.run_installer(
            [sys.executable, "-c", "import time; time.sleep(30)"], environment, timeout=0.05
        )


def test_restart_keeps_configuration_and_bypasses_path_shadowing(service, monkeypatch):
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/old/install")
    monkeypatch.setenv("SNOWFLAKE_HOME", "/same/connection/settings")
    monkeypatch.setattr(os, "execve", lambda *args: calls.append(args))
    args = parser().parse_args(["--snow-executable", "/custom/snow", "tui"])
    command = Path("/opt/homebrew/opt/snowbeam/bin/snowbeam")
    upgrader.restart_app(command, args, service)
    executable, arguments, environment = calls[0]
    assert executable == command
    assert arguments == [
        str(command),
        "--state-dir",
        str(service.store.directory.resolve()),
        "--snow-config",
        str(service.config.path),
        "--fleet-config",
        str(service.fleet.config.path),
        "--snow-executable",
        "/custom/snow",
        "tui",
    ]
    assert environment["SNOWFLAKE_HOME"] == "/same/connection/settings"
    assert "PYTHONPATH" not in environment


def setup_panel(service, monkeypatch, install):
    service.preferences.set_refresh_minutes(service.config.profile("work").key, 0)
    Updates(service.store.directory).path.write_text(
        json.dumps({"latest": "9.0", "checked_at": iso()})
    )
    installer = SimpleNamespace(install=install)
    monkeypatch.setattr("snowbeam.tui.homebrew_upgrader", lambda: installer)
    monkeypatch.setattr("snowbeam.updates_tui.homebrew_upgrader", lambda: installer)
    return Snowbeam(service)


async def test_update_button_runs_installer_and_requests_restart(service, monkeypatch):
    complete = Event()
    started = Event()

    def install(version, progress):
        assert version == "9.0"
        progress("Installing Snowbeam 9.0…")
        started.set()
        assert complete.wait(10)
        return upgrader.Restart(Path("/brew/opt/snowbeam/bin/snowbeam"))

    app = setup_panel(service, monkeypatch, install)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("u")
            await pilot.pause()
            button = app.screen.query_one("#install-update", Button)
            assert button.display and not button.disabled
            assert button.region.bottom <= 24
            button.press()
            await pilot.pause()
            assert started.is_set()
            assert app.upgrading and app.busy
            assert app.screen.query_one("#close-updates", Button).disabled
            app.action_quit()
            assert app.upgrading
            complete.set()
            # Textual cancels workers as the successful restart closes the app.
            with suppress(WorkerCancelled):
                await app.workers.wait_for_complete()
        assert isinstance(app.return_value, upgrader.Restart)
    finally:
        complete.set()


async def test_failed_update_stays_in_app_and_can_retry(service, monkeypatch):
    def install(version, progress):
        raise upgrader.UpgradeError("Homebrew could not complete the update.")

    app = setup_panel(service, monkeypatch, install)
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("u")
        await pilot.pause()
        app.screen.query_one("#install-update", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert not app.upgrading and not app.busy
        assert not app.screen.query_one("#install-update", Button).disabled
        assert "could not complete" in str(app.screen.query_one("#install-status", Static).render())


async def test_active_snowflake_operation_cannot_be_interrupted_for_update(service, monkeypatch):
    app = setup_panel(service, monkeypatch, lambda *args: pytest.fail("Unexpected installer"))
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.press("u")
        await pilot.pause()
        app.busy = True
        app.install_update()
        assert app.busy and not app.upgrading
        assert "current operation" in str(app.screen.query_one("#install-status", Static).render())
        app.busy = False
