"""Verify a real wheel upgrade retains isolated synthetic configuration and state."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous_wheel", type=Path)
    parser.add_argument("new_wheel", type=Path)
    args = parser.parse_args()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SNOWFLAKE_") and key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    with tempfile.TemporaryDirectory(prefix="snowbeam-upgrade-") as temporary:
        directory = Path(temporary)
        python = directory / "venv/bin/python"
        command = directory / "venv/bin/snowbeam"

        def run(*arguments):
            return subprocess.run(arguments, cwd=directory, env=environment, check=True)

        run("uv", "venv", "--python", "3.13", str(directory / "venv"))
        run("uv", "pip", "install", "--python", str(python), str(args.previous_wheel.resolve()))
        run(
            str(python),
            "-c",
            """
import json
from pathlib import Path
from snowbeam.demo import demo_service
service = demo_service(Path("synthetic"))
service.store.save_operation(
    "upgrade-proof", "test", "inspect", {"user":"SYNTHETIC"}, "complete", []
)
Path("before.json").write_text(json.dumps({
    "connections": service.store.connections(), "tokens": service.store.tokens(),
    "operations": service.store.operations(), "config": service.config.path.read_text(),
    "fleet": service.fleet.config.path.read_text(),
}))
""",
        )
        run(
            "uv",
            "pip",
            "install",
            "--reinstall-package",
            "snowbeam",
            "--python",
            str(python),
            str(args.new_wheel.resolve()),
        )
        run(str(command), "--version")
        run(
            str(python),
            "-c",
            """
import json
from pathlib import Path
from snowbeam.config import Config
from snowbeam.service import Service
from snowbeam.store import Store
service = Service(Config(Path("synthetic/snowflake/config.toml")), Store(Path("synthetic/data")))
before = json.loads(Path("before.json").read_text())
assert service.store.connections() == before["connections"]
assert service.store.tokens() == before["tokens"]
assert service.store.operations() == before["operations"]
assert service.config.path.read_text() == before["config"]
assert service.fleet.config.path.read_text() == before["fleet"]
assert not service.preferences.automatic_updates()
with service.store.db() as db:
    assert db.execute("PRAGMA user_version").fetchone()[0] == 4
account_id = service.store.accounts()[0]["id"]
service.store.set_labels("organization", "ACME", alias="Acme Accounting LLC")
service.store.set_labels(
    "account", account_id, alias="After upgrade", notes="Line one.\\nLine two."
)
reopened = Store(service.store.directory)
assert reopened.labels("organization", "ACME")["alias"] == "Acme Accounting LLC"
assert reopened.labels("account", account_id)["notes"] == "Line one.\\nLine two."
print("Upgrade retained profiles, identities, tokens, and operations; local labels persist.")
""",
        )
        run(
            str(command),
            "--state-dir",
            str(directory / "synthetic/data"),
            "--snow-config",
            str(directory / "synthetic/snowflake/config.toml"),
            "updates",
            "status",
            "--json",
        )


if __name__ == "__main__":
    main()
