"""An isolated, synthetic inventory. Never opens the user's Snowflake configuration."""

from datetime import timedelta
from pathlib import Path

from .config import Config, Profile
from .service import Service
from .snowflake import SnowClient
from .store import Store, iso, utcnow


class DemoClient(SnowClient):
    def __init__(self):
        super().__init__(executable="demo")

    def identity(self, profile: Profile, **_) -> dict:
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
            for name in ("production-deploy", "development")
        ]


def demo_service(directory: Path) -> Service:
    config = Config(directory / "snowflake/config.toml")
    for name, account, user in (
        ("production-deploy", "ACME-PRODUCTION", "DEPLOY_BOT"),
        ("development", "ACME-DEVELOPMENT", "JANE"),
    ):
        config.save(
            name,
            {"account": account, "user": user, "authenticator": "PROGRAMMATIC_ACCESS_TOKEN"},
            create=True,
        )
    config.set_default("development")
    service = Service(config, Store(directory / "data"), DemoClient())
    service.demo = True
    service.refresh()
    service.store.bind_token(config.profile("production-deploy").key, "DEPLOYMENT")
    return service
