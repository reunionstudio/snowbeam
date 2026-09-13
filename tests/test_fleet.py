import json
from datetime import timedelta

import pytest

from snowbeam.fleet import FleetConfig, identifier, quote
from snowbeam.security import provisioning_policy_issues
from snowbeam.store import iso, utcnow

TEMPLATE = {
    "organization": "ACME",
    "account": "PROD",
    "admin_connection": "work",
    "role": "ANALYST_AGENT",
    "network_policy": "RUNNER_NETWORK",
    "authentication_policy": "SECURITY.POLICIES.PAT_ONLY",
    "auth": "PAT",
    "provider": "onepassword",
    "vault": "agent-vault",
    "vault_account": "acme-account",
    "days_to_expiry": 30,
}
AUTH = {
    "policy": "SECURITY.POLICIES.PAT_ONLY",
    "methods": ["PROGRAMMATIC_ACCESS_TOKEN"],
    "properties": {
        "AUTHENTICATION_METHODS": "[PROGRAMMATIC_ACCESS_TOKEN]",
        "PAT_POLICY": "{NETWORK_POLICY_EVALUATION=ENFORCED_REQUIRED, MAX_EXPIRY_IN_DAYS=90, "
        "REQUIRE_ROLE_RESTRICTION_FOR_SERVICE_USERS=true}",
    },
}
NETWORK = {
    "policy": "RUNNER_NETWORK",
    "level": "USER",
    "properties": {"ALLOWED_IP_LIST": "198.51.100.10/32", "BLOCKED_IP_LIST": ""},
    "rules": [],
}


def agent(service):
    fleet = service.fleet
    fleet.config.save("templates", "reporting", TEMPLATE, create=True)
    fleet.config.add_agent(
        "sam",
        "reporting",
        "AGENT_SAM",
        "sam",
        client="Acme",
        owner="Alice",
        runtime="runner-east",
        purpose="Daily reporting",
    )
    return fleet


def test_intent_remains_separate_from_standard_connections(service):
    before = service.config.source.read_bytes()
    fleet = agent(service)
    assert fleet.config.spec("sam")["role"] == "ANALYST_AGENT"
    assert service.config.source.read_bytes() == before
    assert fleet.config.path.name == "snowbeam.toml"
    assert fleet.config.path.stat().st_mode & 0o777 == 0o600
    row = next(r for r in fleet.identities() if r["id"] == "sam")
    assert row["status"] == "Needs attention"
    assert row["connection_health"] == "not_created"
    assert len(fleet.identities(client="Acme")) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"token": "secret"},
        {"password": "secret"},
        {"private_key": "secret"},
        {"sql": "GRANT ROLE ACCOUNTADMIN"},
    ],
)
def test_manifest_rejects_secret_and_arbitrary_execution_fields(service, extra):
    with pytest.raises(ValueError, match="Unknown Snowbeam fields"):
        service.fleet.config.save("templates", "bad", {**TEMPLATE, **extra})
    assert not service.fleet.config.path.exists()


def test_template_and_identity_boundaries(service):
    fleet = agent(service)
    with pytest.raises(ValueError, match="already tracked"):
        fleet.config.add_agent("duplicate", "reporting", "AGENT_SAM", "another")
    with pytest.raises(ValueError, match="unused"):
        fleet.config.add_agent("duplicate", "reporting", "OTHER", "work")
    with pytest.raises(ValueError, match="administrative"):
        fleet.config.save("templates", "bad", {**TEMPLATE, "role": "ACCOUNTADMIN"})
    with pytest.raises(ValueError):
        identifier('SAM"; DROP USER ALICE; --')
    assert quote('odd"name') == '"odd""name"'


def test_unknown_manifest_versions_and_fields_are_rejected(tmp_path):
    path = tmp_path / "fleet.toml"
    path.write_text("version = 42\n")
    with pytest.raises(Exception, match="Unsupported"):
        FleetConfig(path).read()


def test_shared_connections_preserve_existing_secrets_comments_and_defaults(service):
    path = service.config.path
    path.write_text(
        '# original\ndefault_connection_name = "work"\n[connections.work]\n# a '
        "credential owned by the existing "
        'config\naccount="ACME-PROD"\nuser="ALICE"\npassword="ORIGINAL_SECRET"\n'
    )
    service.config.ensure_shared()
    assert service.config.source.name == "connections.toml"
    assert "ORIGINAL_SECRET" in service.config.source.read_text()
    assert "# a credential" in service.config.source.read_text()
    service.config.save("sam", {"account": "ACME-PROD", "user": "SAM"}, create=True)
    assert [p.name for p in service.config.profiles()] == ["sam", "work"]
    assert service.config.profile("work").is_default


def test_security_cache_preserves_failed_evidence_and_drops_secrets(service):
    service.refresh()
    row = service.store.connections()[0]
    account = row["account_id"]
    service.store.save_security(
        account,
        "ALICE",
        {
            "user": {
                "status": "ok",
                "data": {"NAME": "ALICE", "TYPE": "PERSON", "PASSWORD": "DONT_CACHE"},
            },
            "tokens": {"status": "ok", "data": [{"name": "TEST", "token_secret": "DONT_CACHE"}]},
        },
    )
    old = service.store.security(account, "ALICE")["user"]
    service.store.save_security(
        account, "ALICE", {"user": {"status": "permission", "message": "Unavailable"}}
    )
    current = service.store.security(account, "ALICE")["user"]
    assert current["data"] == old["data"]
    assert current["checked_at"] == old["checked_at"]
    assert current["status"] == "permission"
    assert b"DONT_CACHE" not in service.store.path.read_bytes()
    with pytest.raises(ValueError, match="Credentials"):
        service.store.save_fleet_state("sam", {"token_secret": "SECRET"})


def test_security_policy_validation_fails_closed():
    assert not provisioning_policy_issues(AUTH, NETWORK, "PAT", 30)
    assert provisioning_policy_issues({**AUTH, "methods": ["ALL"]}, NETWORK, "PAT", 30)
    assert provisioning_policy_issues(AUTH, NETWORK, "PAT", 91)
    assert provisioning_policy_issues(
        AUTH, {**NETWORK, "properties": {"ALLOWED_IP_LIST": "0.0.0.0/0"}}, "PAT", 30
    )
    assert provisioning_policy_issues({**AUTH, "properties": {}}, NETWORK, "PAT", 30)


def test_security_staleness_is_not_reported_as_verified(service):
    service.refresh()
    fleet = service.fleet
    fleet.config.track("alice", "work", client="Acme")
    row = service.store.connections()[0]
    sections = {
        "user": {"NAME": "ALICE", "TYPE": "PERSON", "DISABLED": False},
        "grants": [],
        "authentication": AUTH,
        "network": NETWORK,
        "tokens": [],
    }
    service.store.save_security(
        row["account_id"], "ALICE", {k: {"status": "ok", "data": v} for k, v in sections.items()}
    )
    assert fleet.identities()[0]["status"] == "Inspected"
    with service.store.db() as db:
        db.execute("UPDATE security_checks SET checked_at=?", (iso(utcnow() - timedelta(days=2)),))
    assert any("24 hours" in issue for issue in fleet.identities()[0]["issues"])
    exported = json.dumps(fleet.identities())
    assert "Acme" in exported


def test_client_filter_does_not_hide_a_shared_reference(service):
    from test_vault import OP

    service.config.save("other", {"account": "NORTHWIND-PROD", "user": "JANE"}, create=True)
    service.import_profiles()
    fleet = service.fleet
    fleet.config.track("acme", "work", client="Acme")
    fleet.config.track("northwind", "other", client="Northwind")
    fleet.config.save("credentials", "acme", OP)
    fleet.config.save("credentials", "northwind", OP)
    rows = fleet.identities(client="Acme")
    assert len(rows) == 1
    assert any("shared" in issue and "northwind" in issue for issue in rows[0]["issues"])


def test_large_fleet_reads_manifest_once(service, monkeypatch):
    import tomlkit

    path = service.fleet.config.path
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        tomlkit.dumps(
            {
                "templates": {"reporting": TEMPLATE},
                "identities": {
                    f"agent-{i}": {
                        "connection": f"agent-{i}",
                        "kind": "agent",
                        "template": "reporting",
                        "user": f"AGENT_{i}",
                        "owner": "Jane",
                        "runtime": f"runner-{i}",
                    }
                    for i in range(1000)
                },
            }
        )
    )
    original = service.fleet.config.read
    calls = []

    def read():
        calls.append(True)
        return original()

    monkeypatch.setattr(service.fleet.config, "read", read)
    rows = service.fleet.identities()
    assert len(rows) == 1001  # 1,000 planned agents plus the existing admin profile.
    assert len(calls) == 1
    assert all(row["status"] == "Needs attention" for row in rows)


def test_direct_manifest_edits_cannot_duplicate_managed_principals(service):
    fleet = agent(service)
    original = fleet.config.path.read_bytes()
    with pytest.raises(ValueError, match="already tracked"):
        fleet.config.save(
            "identities",
            "other-sam",
            {
                "kind": "agent",
                "connection": "another",
                "template": "reporting",
                "user": "AGENT_SAM",
                "owner": "Another owner",
                "runtime": "Another runtime",
            },
        )
    assert fleet.config.path.read_bytes() == original
