from datetime import timedelta

import pytest

from snowbeam.config import ConfigError
from snowbeam.store import Store, utcnow


def test_background_disabled_manual_refresh_still_works(service):
    key = service.config.profile("work").key
    service.preferences.set_refresh_minutes(key, 0)
    assert not service.refresh_due()
    assert service.refresh(interactive=False, scheduled=True).refreshed == 0
    assert not service.client.calls
    assert service.refresh("work").refreshed == 1


def test_scheduled_refresh_waits_backs_off_and_preserves_stale_metadata(service, monkeypatch):
    now = utcnow()
    monkeypatch.setattr("snowbeam.store.utcnow", lambda: now)
    assert service.refresh(interactive=False, scheduled=True).refreshed == 1
    previous = service.store.connections()[0]["checked_at"]
    assert not service.refresh_due()
    calls = list(service.client.calls)
    assert service.refresh(interactive=False, scheduled=True).refreshed == 0
    assert service.client.calls == calls
    now += timedelta(hours=1)
    assert service.refresh_due()
    service.client.fail_identity = True
    assert service.refresh(interactive=False, scheduled=True).issues
    assert service.store.connections()[0]["checked_at"] == previous
    assert service.store.tokens()
    now += timedelta(minutes=119)
    assert not service.refresh_due()
    now += timedelta(minutes=1)
    assert service.refresh_due()
    service.client.fail_identity = False
    assert service.refresh(interactive=False, scheduled=True).refreshed == 1
    now += timedelta(hours=1)
    assert service.refresh_due()


def test_changed_profile_or_interval_becomes_due_and_browser_auth_is_never_background(service):
    assert service.refresh().refreshed == 1
    assert not service.refresh_due()
    key = service.config.profile("work").key
    service.preferences.set_refresh_minutes(key, 120)
    assert service.refresh_due()
    assert service.refresh().refreshed == 1
    service.config.save("work", {"user": "OTHER"})
    assert service.refresh_due()
    assert service.store.connections()[0]["status"] == "not_checked"
    service.config.save("work", {"authenticator": "externalbrowser"})
    assert not service.refresh_due()
    calls = list(service.client.calls)
    assert service.refresh(interactive=False, scheduled=True).refreshed == 0
    assert service.client.calls == calls


def test_preferences_preserve_unknown_fields_and_reject_invalid_intervals(service):
    preferences = service.preferences
    preferences.path.write_text('# keep me\n[future]\nvalue = "retained"\n')
    preferences.set_refresh_minutes("config::work", 30)
    preferences.set_automatic_updates(True)
    assert "# keep me" in preferences.path.read_text()
    assert preferences.read()["future"]["value"] == "retained"
    for value in (-1, 1441, True):
        with pytest.raises(ConfigError):
            preferences.set_refresh_minutes("config::work", value)


def test_cache_upgrade_retains_inventory_and_journal(service):
    service.refresh()
    service.store.save_operation(
        "synthetic-operation", "test", "inspect", {"user": "SYNTHETIC"}, "complete", []
    )
    original = service.store.connections()
    journal = service.store.operations()
    with service.store.db() as db:
        db.execute("DROP TABLE refresh_schedule")
        db.execute("PRAGMA user_version = 2")
    upgraded = Store(service.store.directory)
    assert upgraded.connections() == original
    assert upgraded.tokens()
    assert upgraded.operations() == journal
    assert upgraded.refresh_due("work", 60)
