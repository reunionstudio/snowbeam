"""An isolated, synthetic inventory. Never opens the user's Snowflake configuration."""

import json
import re
from datetime import timedelta
from pathlib import Path

from .config import Config, Profile
from .service import Service
from .snowflake import SnowClient, SnowError
from .store import Store, iso, utcnow


class DemoClient(SnowClient):
    def __init__(self):
        super().__init__(executable="demo")

    def identity(self, profile: Profile, **_) -> dict:
        if profile.name == "northwind":
            return {
                "organization_name": "NORTHWIND",
                "account_name": "ANALYTICS",
                "account_locator": "CD24680",
                "region": "AWS_EU_WEST_1",
                "user_name": "JANE_CONSULTANT",
                "role_name": "DATA_REVIEWER",
                "warehouse_name": "REVIEW_XS",
            }
        production = profile.name == "production-deploy"
        return {
            "organization_name": "ACME",
            "account_name": "PRODUCTION" if production else "DEVELOPMENT",
            "account_locator": "XY12345" if production else "AB67890",
            "region": "AWS_US_EAST_1",
            "user_name": "DEPLOY_BOT" if production else "JANE",
            "role_name": "DEPLOYER" if production else "ANALYST",
            "warehouse_name": "COMPUTE_XS",
        }

    def pats(self, profile: Profile, **_) -> list[dict]:
        identity = self.identity(profile)
        if profile.name == "northwind":
            return []
        entries = (
            [("DEPLOYMENT", 3)]
            if profile.name == "production-deploy"
            else [("DEVELOPMENT", 28), ("OLD_REPORTING", -1)]
        )
        return [
            {
                "name": name,
                "user_name": identity["user_name"],
                "expires_at": iso(utcnow() + timedelta(days=days, minutes=-5)),
                "status": "ACTIVE" if days > 0 else "EXPIRED",
                "role_restriction": identity["role_name"],
            }
            for name, days in entries
        ]

    def snapshot(self, profile: Profile, **_) -> tuple[dict, list[dict]]:
        return self.identity(profile), self.pats(profile)

    def accounts(self, profile: Profile, **_) -> list[dict]:
        return [
            self.identity(Profile(name, profile.config_path, profile.source_path, {}))
            for name in ("production-deploy", "development", "northwind")
        ]

    def _output(self, profile, sql, **kwargs):
        # Fixed synthetic responses: no subprocess or account access in demo mode.
        statement = sql.rsplit("; ", 1)[-1]
        who = self.identity(profile)
        user = re.search(r'(?:USER|FOR USER) "([^"]+)"$', statement)
        username = user[1] if user else who["user_name"]
        if statement.startswith("DESCRIBE USER"):
            rows = [
                {"property": k, "property_value": v}
                for k, v in {
                    "NAME": username,
                    "TYPE": "SERVICE_AGENT"
                    if username in {"DEPLOY_BOT", "AGENT_REPORTER"}
                    else "PERSON",
                    "DISABLED": username == "AGENT_REPORTER",
                }.items()
            ]
        elif statement.startswith("SHOW GRANTS TO USER"):
            rows = [{"role": who["role_name"], "grantee_name": username}]
        elif statement.startswith("SHOW GRANTS TO ROLE"):
            rows = [{"privilege": "SELECT", "granted_on": "VIEW", "name": "REPORTING.PUBLIC.DAILY"}]
        elif statement.startswith("SHOW AUTHENTICATION POLICIES"):
            rows = [{"database_name": "SECURITY", "schema_name": "POLICIES", "name": "PAT_ONLY"}]
        elif statement.startswith("DESCRIBE AUTHENTICATION POLICY"):
            rows = [
                {"property": k, "value": v}
                for k, v in {
                    "AUTHENTICATION_METHODS": "[SAML]"
                    if profile.name == "northwind"
                    else "[PROGRAMMATIC_ACCESS_TOKEN]",
                    "PAT_POLICY": (
                        "{NETWORK_POLICY_EVALUATION=ENFORCED_REQUIRED, MAX_EXPIRY_IN_DAYS=90}"
                    ),
                }.items()
            ]
        elif statement.startswith("SHOW PARAMETERS"):
            rows = [{"key": "NETWORK_POLICY", "value": "OFFICE_OR_RUNNER", "level": "USER"}]
        elif statement.startswith("DESCRIBE NETWORK POLICY"):
            rows = [{"name": "ALLOWED_IP_LIST", "value": "198.51.100.10/32"}]
        elif statement.startswith("SHOW USER PROGRAMMATIC ACCESS TOKENS"):
            rows = self.pats(profile) if username == who["user_name"] else []
        elif statement.startswith("SELECT EVENT_TIMESTAMP"):
            rows = []
        else:
            raise SnowError("demo", "This operation has no synthetic demo response.")
        return json.dumps([[{"status": "Synthetic demo only"}], rows])


def demo_service(directory: Path) -> Service:
    config = Config(directory / "snowflake/config.toml")
    for name, account, user in (
        ("production-deploy", "ACME-PRODUCTION", "DEPLOY_BOT"),
        ("development", "ACME-DEVELOPMENT", "JANE"),
        ("northwind", "NORTHWIND-ANALYTICS", "JANE_CONSULTANT"),
    ):
        config.save(
            name,
            {
                "account": account,
                "user": user,
                "authenticator": "externalbrowser"
                if name == "northwind"
                else "PROGRAMMATIC_ACCESS_TOKEN",
            },
            create=True,
        )
    config.set_default("development")
    service = Service(config, Store(directory / "data"), DemoClient())
    service.demo = True
    service.refresh()
    service.store.bind_token(config.profile("production-deploy").key, "DEPLOYMENT")
    fleet = service.fleet
    for key, connection, kind, client, runtime, purpose in (
        ("jane-acme", "development", "person", "Acme", "consultant-laptop", "Client data work"),
        (
            "deploy-bot",
            "production-deploy",
            "agent",
            "Acme",
            "production-runner",
            "Deploy approved changes",
        ),
        (
            "jane-northwind",
            "northwind",
            "person",
            "Northwind",
            "consultant-laptop",
            "Review reporting access",
        ),
    ):
        fleet.config.track(
            key,
            connection,
            kind=kind,
            client=client,
            owner="Jane",
            runtime=runtime,
            purpose=purpose,
        )
    fleet.config.save(
        "templates",
        "acme-reporting",
        {
            "organization": "ACME",
            "account": "DEVELOPMENT",
            "admin_connection": "development",
            "role": "ANALYST",
            "authentication_policy": "SECURITY.POLICIES.PAT_ONLY",
            "network_policy": "OFFICE_OR_RUNNER",
            "auth": "PAT",
            "days_to_expiry": 30,
            "provider": "onepassword",
            "vault": "synthetic-agent-vault",
            "vault_account": "synthetic-account",
        },
        create=True,
    )
    fleet.config.add_agent(
        "reporter",
        "acme-reporting",
        "AGENT_REPORTER",
        "reporter",
        owner="Jane",
        runtime="reports-runner",
        client="Acme",
        purpose="Daily reports; setup pending",
    )
    fleet.refresh()
    return service
