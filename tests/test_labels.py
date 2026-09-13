import json

import pytest
from conftest import IDENTITY

from snowbeam.cli import main
from snowbeam.labels import ALIAS_LIMIT, NOTES_LIMIT
from snowbeam.store import Store


def test_labels_survive_refresh_restart_and_account_rename(service):
    identity = dict(IDENTITY, organization_name="FLXH5C4T")
    account_id = service.store.ensure_account(identity)
    original_config = service.config.path.read_bytes()
    service.store.set_labels(
        "organization", "FLXH5C4T", alias="Acme Accounting LLC", notes="Client since 2026."
    )
    notes = "Monthly close.\nOwner: finance team.\n[Review] before deployment."
    service.store.set_labels("account", account_id, alias="Finance production", notes=notes)
    service.store.save_accounts("FLXH5C4T", [identity])
    reopened = Store(service.store.directory)
    account = reopened.accounts()[0]
    assert account["organization_alias"] == "Acme Accounting LLC"
    assert account["organization_notes"] == "Client since 2026."
    assert account["account_alias"] == "Finance production"
    assert account["account_notes"] == notes
    assert account["organization"] == "FLXH5C4T"
    assert account["name"] == IDENTITY["account_name"]
    assert service.config.path.read_bytes() == original_config
    assert not service.client.calls

    renamed = dict(identity, organization_name="NEW_ORG", account_name="RENAMED")
    assert reopened.ensure_account(renamed) == account_id
    account = reopened.accounts()[0]
    assert account["account_alias"] == "Finance production"
    assert account["account_notes"] == notes
    assert account["organization_alias"] == ""  # Never guess that an organization moved.
    assert reopened.labels("account", account_id)["identifier"] == "NEW_ORG-RENAMED"


def test_account_labels_are_scoped_by_stable_account_id(service):
    first = service.store.ensure_account(IDENTITY)
    second = service.store.ensure_account(
        dict(IDENTITY, organization_name="OTHER", region="AWS_US_WEST_2")
    )
    service.store.set_labels("account", first, alias="One", notes="First account")
    service.store.set_labels("account", second, alias="Two", notes="Second account")
    service.store.set_labels("account", first, alias="")
    assert service.store.labels("account", first)["notes"] == "First account"
    assert service.store.labels("account", first)["alias"] == ""
    assert service.store.labels("account", second)["alias"] == "Two"
    service.store.set_labels("account", first, notes="")
    assert service.store.labels("account", first)["notes"] == ""


def test_schema_three_migrates_without_losing_inventory(service):
    account_id = service.store.ensure_account(IDENTITY)
    with service.store.db() as db:
        db.execute("DROP TABLE account_labels")
        db.execute("DROP TABLE organization_labels")
        db.execute("PRAGMA user_version=3")
    upgraded = Store(service.store.directory)
    assert upgraded.accounts()[0]["id"] == account_id
    upgraded.set_labels("account", account_id, alias="After upgrade")
    with upgraded.db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
    assert Store(service.store.directory).labels("account", account_id)["alias"] == "After upgrade"


@pytest.mark.parametrize(
    "fields",
    [
        {"alias": "a" * (ALIAS_LIMIT + 1)},
        {"alias": "Line\nbreak"},
        {"alias": "\x1b[31mred"},
        {"notes": "\x9b31mcontrol"},
        {"notes": "n" * (NOTES_LIMIT + 1)},
    ],
)
def test_invalid_labels_do_not_overwrite_saved_values(service, fields):
    key = service.store.ensure_account(IDENTITY)
    service.store.set_labels("account", key, alias="Original", notes="Original notes")
    with pytest.raises(ValueError):
        service.store.set_labels("account", key, **fields)
    assert service.store.labels("account", key)["alias"] == "Original"
    assert service.store.labels("account", key)["notes"] == "Original notes"


def test_unknown_targets_are_rejected(service):
    for kind, key in [("organization", "UNKNOWN"), ("account", "UNKNOWN"), ("other", "UNKNOWN")]:
        with pytest.raises(ValueError):
            service.store.set_labels(kind, key, alias="Test")


def test_cli_reads_edits_and_clears_labels_without_connecting(service, capsys):
    service.store.ensure_account(IDENTITY)
    args = ["--snow-config", str(service.config.path), "--state-dir", str(service.store.directory)]
    command = args + ["labels", "organization", "ACME", "--json"]
    assert (
        main(command + ["--alias", "Acme Accounting LLC", "--notes", "Line one.\nLine two."]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["identifier"] == "ACME"
    assert result["alias"] == "Acme Accounting LLC"
    assert main(command + ["--alias", ""]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["alias"] == ""
    assert result["notes"] == "Line one.\nLine two."
    assert main(args + ["labels", "account", "ACME-PROD", "--alias", "Production", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["alias"] == "Production"
    assert main(args + ["inventory", "--json"]) == 0
    account = json.loads(capsys.readouterr().out)["accounts"][0]
    assert account["name"] == "PROD"
    assert account["account_alias"] == "Production"
    assert not service.client.calls
