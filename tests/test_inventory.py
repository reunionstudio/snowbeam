from datetime import timedelta

import pytest
from conftest import IDENTITY, NOW, TOKEN

from snowdock.store import expiry_label, iso, stale


def seed(service):
    profile = service.config.profile("work")
    account = service.store.connected(profile, IDENTITY, NOW)
    service.store.save_tokens(account, "ALICE", [TOKEN], NOW)
    return account


def test_expirations_survive_failed_authentication_and_restart(service):
    account = seed(service)
    service.client.fail_identity = True
    result = service.refresh()
    assert result.refreshed == 0
    assert service.store.connections()[0]["status"] == "expired"
    from snowdock.store import Store

    reopened = Store(service.store.directory)
    alert = reopened.alerts(now=NOW + timedelta(days=2))[0]
    assert alert["account_id"] == account
    assert alert["expiry_label"] == "In 1 day"
    assert alert["stale"]
    assert alert["checked_at"] == iso(NOW)


def test_permission_error_retains_tokens_and_marks_inventory_unknown(service):
    seed(service)
    service.client.fail_tokens = True
    service.refresh()
    assert service.store.tokens()[0]["name"] == "WORK"
    assert service.store.tokens()[0]["inventory_status"] == "permission"
    assert any("cannot inspect" in value for value in service.coverage_issues())


def test_empty_success_marks_absent_not_revoked(service):
    account = seed(service)
    service.store.save_tokens(account, "ALICE", [], NOW)
    assert service.store.tokens() == []
    retained = service.store.tokens(include_unlisted=True)[0]
    assert retained["listed"] == 0
    assert retained["status"] == "ACTIVE"


def test_profile_aliases_share_account_and_token_inventory(service):
    service.config.save(
        "alias",
        {
            "account": "XY12345.us-east-1",
            "user": "ALICE",
            "authenticator": "PROGRAMMATIC_ACCESS_TOKEN",
        },
        create=True,
    )
    service.refresh()
    assert len(service.store.connections()) == 2
    assert len(service.store.accounts()) == 1
    assert len(service.store.tokens()) == 1
    assert len([call for call in service.client.calls if call[0] == "pats"]) == 2


def test_renamed_account_keeps_token_relationships(service):
    account = seed(service)
    changed = dict(IDENTITY, organization_name="NEWORG", account_name="RENAMED")
    service.store.connected(service.config.profile("work"), changed, NOW)
    assert len(service.store.accounts()) == 1
    assert service.store.accounts()[0]["id"] == account
    assert service.store.tokens()[0]["organization"] == "NEWORG"


def test_same_account_name_and_locator_in_different_region_is_distinct(service):
    seed(service)
    profile = service.config.profile("work")
    service.store.connected(
        profile, dict(IDENTITY, organization_name="OTHER", region="AWS_EU_WEST_1"), NOW
    )
    assert len(service.store.accounts()) == 2


def test_explicit_binding_and_changed_settings(service):
    seed(service)
    profile = service.config.profile("work")
    with pytest.raises(ValueError, match="Choose a token"):
        service.store.bind_token(profile.key, "NONEXISTENT")
    service.store.bind_token(profile.key, "WORK")
    assert service.store.alerts(now=NOW)[0]["connections"] == ["work"]
    service.config.save("work", {"account": "ACME-OTHER"})
    service.import_profiles()
    row = service.store.connections()[0]
    assert row["account_id"] is None
    assert row["token_name"] is None
    assert row["status"] == "not_checked"


def test_timezone_conversion_and_expiry_boundaries(service):
    account = seed(service)
    token = dict(TOKEN, expires_at="2026-09-08T08:00:00-04:00")
    service.store.save_tokens(account, "ALICE", [token], NOW)
    assert service.store.tokens()[0]["expires_at"] == iso(NOW)
    assert service.store.alerts(now=NOW)[0]["expiry_label"] == "Expired"
    assert expiry_label(iso(NOW + timedelta(seconds=1)), NOW) == "Under 1 day"
    assert stale(iso(NOW - timedelta(hours=25)), NOW)


def test_invalid_expiration_is_unknown_not_healthy(service):
    account = seed(service)
    service.store.save_tokens(
        account, "ALICE", [dict(TOKEN, expires_at="2026-09-12 00:00:00")], NOW
    )
    assert service.store.alerts(now=NOW)[0]["expiry_label"] == "Unknown expiry"


def test_notification_threshold_changes_without_contacting_snowflake(service):
    account = seed(service)
    service.store.save_tokens(
        account, "ALICE", [dict(TOKEN, expires_at=iso(NOW + timedelta(days=20)))], NOW
    )
    assert service.store.alerts(now=NOW) == []
    early = service.store.alerts(now=NOW + timedelta(days=6))[0]
    service.store.mark_notice(early["notice_key"])
    assert service.store.notice_sent(early["notice_key"])
    later = service.store.alerts(now=NOW + timedelta(days=13))[0]
    assert not service.store.notice_sent(later["notice_key"])
    rotated = dict(TOKEN, expires_at=iso(NOW + timedelta(days=60)))
    service.store.save_tokens(account, "ALICE", [rotated], NOW)
    assert service.store.alerts(now=NOW + timedelta(days=13)) == []


def test_wrong_user_response_does_not_destroy_cached_inventory(service):
    account = seed(service)
    with pytest.raises(ValueError, match="authenticated user"):
        service.store.save_tokens(account, "ALICE", [dict(TOKEN, user_name="BOB")], NOW)
    assert service.store.tokens()[0]["user_name"] == "ALICE"


def test_empty_cache_is_not_healthy(service):
    assert any("not checked" in issue for issue in service.coverage_issues())
    assert any("PAT inventory" in issue for issue in service.coverage_issues())


def test_background_refresh_does_not_launch_browser(service):
    service.config.save("work", {"authenticator": "externalbrowser"})
    result = service.refresh(interactive=False)
    assert result.refreshed == 0
    assert service.client.calls == []


def test_discovered_accounts_need_no_local_connection(service):
    service.refresh(organization=True)
    assert len(service.store.accounts()) == 2
    assert len(service.store.connections()) == 1


def test_only_safe_metadata_enters_sqlite(service):
    source = service.config.path
    source.write_text(source.read_text() + 'password="SECRET-PASSWORD"\ntoken="SECRET-TOKEN"\n')
    service.client.token_rows = [dict(TOKEN, secret="SECRET-SERVER-TOKEN")]
    service.refresh()
    raw = service.store.path.read_bytes()
    for secret in (b"SECRET-PASSWORD", b"SECRET-TOKEN", b"SECRET-SERVER-TOKEN"):
        assert secret not in raw


def test_missing_bound_token_requires_attention(service):
    account = seed(service)
    service.store.bind_token(service.config.profile("work").key, "WORK")
    service.store.save_tokens(account, "ALICE", [], NOW)
    assert any("associated PAT" in issue for issue in service.coverage_issues())
