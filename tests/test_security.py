import json

import pytest

from snowbeam.security import account_guard, guarded_query, properties
from snowbeam.snowflake import SnowError


@pytest.mark.parametrize("output", ['{"token_secret":"NEVER_ECHO"}', "[]", "[[]]", "[[],{},[]]"])
def test_guarded_query_rejects_malformed_results_without_echo(service, output):
    class Client:
        def _output(self, profile, sql, **kwargs):
            assert sql.startswith("EXECUTE IMMEDIATE")
            assert "CURRENT_ACCOUNT_NAME()" in sql
            assert "CURRENT_ORGANIZATION_NAME()" in sql
            assert "CURRENT_USER()" in sql
            assert sql.endswith("; DESCRIBE USER ALICE")
            return output

    with pytest.raises(SnowError) as error:
        guarded_query(
            Client(),
            service.config.profile("work"),
            "ACME",
            "PROD",
            "DESCRIBE USER ALICE",
            user="ALICE",
        )
    assert "NEVER_ECHO" not in str(error.value)


def test_sensitive_guarded_command_uses_only_private_log_disabled_path(service):
    class Client:
        def secure_output(self, profile, sql, **kwargs):
            return json.dumps([[{"status": "ok"}], [{"token_secret": "TRANSIENT"}]])

        def _output(self, *args, **kwargs):
            raise AssertionError("Sensitive query used ordinary CLI logging")

    rows = guarded_query(
        Client(),
        service.config.profile("work"),
        "ACME",
        "PROD",
        "SELECT 'synthetic'",
        sensitive=True,
    )
    assert rows == [{"token_secret": "TRANSIENT"}]
    assert "COALESCE" in account_guard("ACME", "PROD")
    assert properties([{"property": "PASSWORD", "value": "TRANSIENT"}], {"NAME"}) == {}
