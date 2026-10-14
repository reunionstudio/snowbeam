from pathlib import Path

import pytest
from test_fleet import TEMPLATE, agent
from test_provision import Cloud

from snowbeam.access_requests import AccessRequestPlanner, import_plan, load_request
from snowbeam.admin_roles import minimum_admin_role
from snowbeam.hygiene import connection_hygiene
from snowbeam.provision import Provisioner
from snowbeam.starters import expand_starter


@pytest.fixture
def governance(service, monkeypatch):
    cloud = Cloud()
    fleet = agent(service)
    service.client = cloud
    monkeypatch.setattr("snowbeam.security.guarded_query", cloud.execute)
    monkeypatch.setattr("snowbeam.provision.guarded_query", cloud.execute)
    fleet.config.set_account_admin("ACME", "PROD", "work")
    fleet.config.save("templates", "reporting", {**TEMPLATE, "auth": "KEYPAIR"}, create=False)
    cloud.policy["properties"]["AUTHENTICATION_METHODS"] = "[KEYPAIR]"

    class Vault:
        def __init__(self, spec):
            self.spec = spec

        def ready(self):
            pass

        def create(self, title, secret):
            cloud.counter += 1
            key = f"00000000-0000-0000-0000-{cloud.counter:012}"
            return {
                "provider": "onepassword",
                "vault": "agent-vault",
                "vault_account": "acme-account",
                "item": key,
            }

        def read(self, reference):
            return {"private_key": "KEY", "passphrase": "x"}

    return fleet, cloud


def sample_request(path: Path):
    path.write_text(
        """
version: 1
requester: devo-agent
purpose: Read-only inspection
owner: Adam
runtime: runner-east
lifetime_end: 2030-01-15
auth: KEYPAIR
identity: devo
user: AGENT_DEVO
role: ANALYST_AGENT
accounts:
  - organization: ACME
    account: PROD
capabilities:
  - show_grants
  - show_users
must_not:
  - write_tables
""".strip()
    )


def test_request_import_includes_lifecycle_plan_and_approval(governance, tmp_path):
    fleet, cloud = governance
    path = tmp_path / "req.yaml"
    sample_request(path)
    plan = import_plan(fleet, path, template="reporting")
    assert plan["kind"] == "access_request"
    assert plan["approval"]
    assert plan["lifecycle_plan"]["approval"]
    assert plan["admin_connection"] == "work"
    assert not plan["starter_sql"]
    assert cloud.users == {}


def test_starter_sql_lists_minimum_admin_roles():
    sql = expand_starter(
        "inspect-only", "DEVO_INSPECT", preferences={}, user_starters={}, params={}
    )
    assert sql[0].startswith("CREATE ROLE")
    assert minimum_admin_role(sql[1]) == "SECURITYADMIN"


def test_apply_request_requires_approval(governance, tmp_path, monkeypatch):
    fleet, cloud = governance
    path = tmp_path / "req.yaml"
    sample_request(path)
    plan = import_plan(fleet, path, template="reporting")
    monkeypatch.setattr(
        "snowbeam.provision.generate_key",
        lambda: (
            {"private_key": "ENC", "passphrase": "p"},
            "cHVibGlj",
            "SHA256:abc",
        ),
    )
    result = AccessRequestPlanner(fleet).apply(plan, plan["approval"])
    assert result["status"] == "awaiting_relay_confirmation"
    Provisioner(fleet).confirm_relay("devo")
    assert fleet.state("devo")["status"] == "awaiting_verification"
    assert fleet.state("devo")["lifetime_end"] == "2030-01-15"
    assert fleet.state("devo")["requested_capabilities"]


def test_verify_capabilities_records_evidence_only(governance, tmp_path, monkeypatch):
    fleet, cloud = governance
    path = tmp_path / "req.yaml"
    sample_request(path)
    plan = import_plan(fleet, path, template="reporting")
    monkeypatch.setattr(
        "snowbeam.provision.generate_key",
        lambda: (
            {"private_key": "ENC", "passphrase": "p"},
            "cHVibGlj",
            "SHA256:abc",
        ),
    )
    apply_result = AccessRequestPlanner(fleet).apply(plan, plan["approval"])
    Provisioner(fleet).confirm_relay("devo")
    import tempfile
    from pathlib import Path

    key_path = Path(tempfile.mkstemp(suffix=".p8")[1])
    key_path.write_text(apply_result["credential_relay"]["fields"]["private_key"])
    report = AccessRequestPlanner(fleet).verify_capabilities(
        "devo", private_key_file=key_path, passphrase="x"
    )
    assert "never applies" in report["notice"].lower()
    assert report["checks"]


def test_connection_hygiene_flags_missing_and_shared_keys(service, tmp_path):
    shared = tmp_path / "shared.p8"
    shared.write_text("key")
    service.config.save(
        "a",
        {
            "account": "ACME-PROD",
            "user": "A",
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": str(shared),
        },
        create=True,
    )
    service.config.save(
        "b",
        {
            "account": "ACME-PROD",
            "user": "B",
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": str(shared),
        },
        create=True,
    )
    service.config.save(
        "c",
        {
            "account": "ACME-PROD",
            "user": "C",
            "authenticator": "SNOWFLAKE_JWT",
            "private_key_file": str(tmp_path / "missing.p8"),
        },
        create=True,
    )
    issues = connection_hygiene(service.config)
    assert any(i["kind"] == "shared_key_file" for i in issues)
    assert any(i["kind"] == "missing_key_file" for i in issues)


def test_load_request_rejects_missing_role_and_starter(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("version: 1\nrequester: x\n")
    with pytest.raises(ValueError):
        load_request(path)
