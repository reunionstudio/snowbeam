from datetime import UTC, datetime

import pytest

from snowbeam.config import Config
from snowbeam.service import Service
from snowbeam.snowflake import SnowError
from snowbeam.store import Store

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
IDENTITY = {
    "organization_name": "ACME",
    "account_name": "PROD",
    "account_locator": "XY12345",
    "region": "AWS_US_EAST_1",
    "user_name": "ALICE",
    "role_name": "ANALYST",
}
TOKEN = {
    "name": "WORK",
    "user_name": "ALICE",
    "status": "ACTIVE",
    "role_restriction": "ANALYST",
    "expires_at": "2026-09-11T12:00:00+00:00",
}


class FakeClient:
    executable = "snow"

    def __init__(self):
        self.identity_rows = dict(IDENTITY)
        self.token_rows = [dict(TOKEN)]
        self.fail_identity = False
        self.fail_tokens = False
        self.calls = []

    def identity(self, profile, **kwargs):
        self.calls.append(("identity", profile.name))
        if self.fail_identity:
            raise SnowError("expired", "Snowflake reports an expired credential.")
        return dict(self.identity_rows)

    def pats(self, profile, **kwargs):
        self.calls.append(("pats", profile.name))
        if self.fail_tokens:
            raise SnowError("permission", "The current role cannot inspect this metadata.")
        return [dict(row) for row in self.token_rows]

    def snapshot(self, profile, **kwargs):
        return self.identity(profile), self.pats(profile)

    def accounts(self, profile, **kwargs):
        return [
            dict(self.identity_rows),
            {
                "organization_name": "ACME",
                "account_name": "DEV",
                "account_locator": "AB67890",
                "snowflake_region": "AWS_EU_WEST_1",
            },
        ]


@pytest.fixture
def service(tmp_path, monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("SNOWFLAKE_"):
            monkeypatch.delenv(name)
    config = Config(tmp_path / "snowflake/config.toml")
    config.save(
        "work",
        {"account": "ACME-PROD", "user": "ALICE", "authenticator": "PROGRAMMATIC_ACCESS_TOKEN"},
        create=True,
    )
    result = Service(config, Store(tmp_path / "data"), FakeClient())
    result.import_profiles()
    return result
