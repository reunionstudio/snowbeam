"""CLI wiring for reviewed managed-agent lifecycle commands."""

import json
from types import SimpleNamespace

import pytest
from test_fleet import TEMPLATE, agent
from test_provision import Cloud

from snowbeam.fleet_cli import run


@pytest.fixture
def lifecycle_cli(service, monkeypatch):
    cloud = Cloud()
    agent(service)
    service.client = cloud
    monkeypatch.setattr("snowbeam.security.guarded_query", cloud.execute)
    monkeypatch.setattr("snowbeam.provision.guarded_query", cloud.execute)

    class Vault:
        def __init__(self, spec):
            self.spec = spec

        def ready(self):
            pass

        def create(self, title, secret):
            cloud.counter += 1
            key = f"00000000-0000-0000-0000-{cloud.counter:012}"
            cloud.secrets[key] = dict(secret)
            return {
                "provider": "onepassword",
                "vault": "agent-vault",
                "vault_account": "acme-account",
                "item": key,
            }

    monkeypatch.setattr("snowbeam.provision.Vault", Vault)
    return service, cloud


def _args(**kwargs):
    defaults = {
        "command": "identities",
        "identity_action": "plan",
        "name": "sam",
        "action": "provision",
        "json": True,
        "approve": None,
        "runtime": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_cli_plan_and_apply_json(lifecycle_cli, capsys):
    service, cloud = lifecycle_cli
    assert run(_args(identity_action="plan"), service) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["action"] == "provision"
    assert plan["approval"]
    assert not cloud.users
    assert run(
        _args(identity_action="apply", approve=plan["approval"]),
        service,
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "awaiting_verification"
    assert cloud.users["AGENT_SAM"]["disabled"] is False
    assert cloud.secrets


def test_cli_apply_rejects_stale_approval(lifecycle_cli):
    service, _ = lifecycle_cli
    from snowbeam.provision import Provisioner

    plan = Provisioner(service.fleet).plan("sam")
    with pytest.raises(ValueError, match="plan changed"):
        run(
            _args(identity_action="apply", approve=plan["approval"] + "0"),
            service,
        )


def test_bitwarden_template_provisions_through_cli(lifecycle_cli, monkeypatch):
    service, cloud = lifecycle_cli
    fleet = service.fleet
    template = {
        k: v
        for k, v in TEMPLATE.items()
        if k not in ("provider", "vault", "vault_account")
    }
    template.update(
        provider="bitwarden",
        vault_server="https://vault.bitwarden.eu",
        vault_user="00000000-0000-0000-0000-000000000099",
    )
    fleet.config.save("templates", "bw", template, create=True)
    fleet.config.add_agent(
        "bw-sam",
        "bw",
        "AGENT_BW",
        "bw-sam",
        client="Acme",
        owner="Alice",
        runtime="runner-east",
    )

    class Vault:
        def __init__(self, spec):
            self.spec = spec

        def ready(self):
            pass

        def create(self, title, secret):
            assert self.spec["provider"] == "bitwarden"
            cloud.counter += 1
            key = f"00000000-0000-0000-0000-{cloud.counter:012}"
            cloud.secrets[key] = dict(secret)
            return {
                "provider": "bitwarden",
                "vault_server": self.spec["vault_server"],
                "vault_user": self.spec["vault_user"],
                "item": key,
                "credential_kind": "PAT",
            }

    monkeypatch.setattr("snowbeam.provision.Vault", Vault)
    from snowbeam.provision import Provisioner

    plan = Provisioner(fleet).plan("bw-sam")
    Provisioner(fleet).apply("bw-sam", "provision", plan["approval"])
    assert cloud.users["AGENT_BW"]["tokens"]
