import io
import json
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError

import pytest
from textual.widgets import Button, Checkbox, Static

from snowbeam import updates
from snowbeam.cli import main
from snowbeam.installation import homebrew_command
from snowbeam.store import iso, utcnow
from snowbeam.tui import Snowbeam
from snowbeam.updates_tui import UpdatePanel


def release(version, **overrides):
    return {"tag_name": "v" + version, "draft": False, "prerelease": "a" in version} | overrides


def test_version_channels_and_untrusted_release_fields():
    releases = [
        release("0.2.0"),
        release("0.3.0a2"),
        release("9.0", draft=True),
        release("0.3.0a10"),
        release("1.0; bad"),
        release("1.0+private"),
    ]
    assert updates.latest_version(releases, "0.1.0") == "0.2.0"
    assert updates.latest_version(releases, "0.2.0a1") == "0.3.0a10"
    assert updates.latest_version([], "0.1.0") is None
    with pytest.raises(ValueError):
        updates.latest_version({"message": "bad"})


def test_release_check_is_bounded_credential_free_and_retains_previous_evidence(
    tmp_path, monkeypatch
):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            return io.BytesIO(json.dumps([release("9.0")]).encode())

    monkeypatch.setenv("GITHUB_TOKEN", "DO-NOT-SEND")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "DO-NOT-SEND")
    monkeypatch.setattr(updates, "build_opener", lambda *args: Opener())
    checker = updates.Updates(tmp_path)
    status = checker.check()
    assert status["available"]
    assert not checker.due()
    assert requests[0][0].full_url == updates.RELEASES_URL
    assert requests[0][1] == 5
    assert "DO-NOT-SEND" not in str(requests[0][0].headers)
    assert "DO-NOT-SEND" not in checker.path.read_text()

    class FailedOpener:
        def open(self, request, timeout):
            raise URLError("raw error DO-NOT-SEND")

    monkeypatch.setattr(updates, "build_opener", lambda *args: FailedOpener())
    failed = checker.check()
    assert failed["latest"] == status["latest"]
    assert failed["checked_at"] == status["checked_at"]
    assert failed["error"] == "unavailable"
    assert "DO-NOT-SEND" not in checker.path.read_text()


def test_invalid_oversized_and_redirected_responses_are_not_trusted(tmp_path, monkeypatch):
    class HugeOpener:
        def open(self, request, timeout):
            return io.BytesIO(b" " * (updates.MAX_RESPONSE + 1))

    monkeypatch.setattr(updates, "build_opener", lambda *args: HugeOpener())
    assert updates.Updates(tmp_path).check()["error"] == "unavailable"
    with pytest.raises(URLError):
        updates.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
    checker = updates.Updates(tmp_path)
    checker.path.write_text('{"latest": "bad", "attempted_at": "bad"}')
    assert checker.status()["latest"] is None
    assert checker.due()


def test_update_preferences_are_opt_in_and_demo_cannot_check(service, monkeypatch, capsys):
    monkeypatch.setattr(updates.Updates, "check", lambda self: pytest.fail("Unexpected network"))
    args = ["--state-dir", str(service.store.directory), "--snow-config", str(service.config.path)]
    assert main(args + ["updates", "status", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["automatic"]
    assert main(args + ["updates", "enable", "--json"]) == 0
    assert service.preferences.automatic_updates()
    assert main(args + ["updates", "disable", "--json"]) == 0
    assert not service.preferences.automatic_updates()
    assert main(["--demo", "updates", "check"]) == 2


async def test_updates_panel_offline_and_available_status(service, monkeypatch):
    checker = updates.Updates(service.store.directory)
    checker.path.write_text(json.dumps({"latest": "9.0", "checked_at": iso()}))
    service.preferences.set_automatic_updates(True)
    monkeypatch.setattr(updates.Updates, "check", lambda self: pytest.fail("Offline network call"))
    app = Snowbeam(service, auto_refresh=False)
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.query_one("#updates", Button).region.right <= 80
        assert str(app.query_one("#updates", Button).label) == "Update available"
        await pilot.press("u")
        await pilot.pause()
        assert isinstance(app.screen, UpdatePanel)
        assert app.screen.query_one("#check-update", Button).disabled
        assert app.screen.query_one("#automatic-updates", Checkbox).disabled
        assert "9.0" in str(app.screen.query_one("#update-status", Static).render())
        await pilot.press("escape")


async def test_manual_check_updates_the_app_without_running_an_installer(service, monkeypatch):
    service.preferences.set_refresh_minutes(service.config.profile("work").key, 0)
    calls = []

    def check(self):
        calls.append(True)
        self.path.write_text(json.dumps({"latest": "9.0", "checked_at": iso()}))
        return self.status()

    monkeypatch.setattr(updates.Updates, "check", check)
    app = Snowbeam(service)
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        assert not calls
        await pilot.press("u")
        await pilot.pause()
        app.screen.query_one("#check-update", Button).press()
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert calls == [True]
        assert "Update available: 9.0" in str(
            app.screen.query_one("#update-status", Static).render()
        )


def test_daily_checks_back_off_and_homebrew_paths_survive_upgrade(tmp_path):
    checker = updates.Updates(tmp_path)
    checker.path.write_text(json.dumps({"attempted_at": iso(utcnow() - timedelta(hours=23))}))
    assert not checker.due()
    checker.path.write_text(json.dumps({"attempted_at": iso(utcnow() - timedelta(hours=25))}))
    assert checker.due()
    for version in ("0.2.0a1", "0.2.0a2"):
        assert homebrew_command(Path(f"/opt/homebrew/Cellar/snowbeam/{version}/libexec")) == Path(
            "/opt/homebrew/opt/snowbeam/bin/snowbeam"
        )
    assert homebrew_command(Path("/private/tmp/venv")) is None
