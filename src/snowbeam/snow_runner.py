"""Run the Snowflake CLI with per-connection passphrases from the OS secret store."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence

from .config import Config
from .os_secrets import SecretStore, default_secret_store

STRIPPED_ENV_VARS = frozenset(
    {
        "PRIVATE_KEY_PASSPHRASE",
        "SNOWFLAKE_PRIVATE_KEY_PASSPHRASE",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_TOKEN",
        "SNOWSQL_PWD",
    }
)


def strip_snowflake_secrets(environment: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if key not in STRIPPED_ENV_VARS
        and not key.startswith(("SNOWFLAKE_", "SNOWSQL_", "BW_", "OP_"))
    }


def parse_snow_connection(argv: Sequence[str], config: Config) -> str | None:
    args = list(argv)
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("-c", "--connection"):
            if index + 1 < len(args):
                return args[index + 1]
            return None
        if arg.startswith("--connection="):
            return arg.split("=", 1)[1] or None
        if arg == "--":
            break
        index += 1
    for profile in config.profiles(environment=True):
        if profile.is_default:
            return profile.name
    profiles = config.profiles(environment=True)
    return profiles[0].name if len(profiles) == 1 else None


def child_environment(
    parent: dict[str, str],
    *,
    passphrase: str | None,
) -> dict[str, str]:
    child = strip_snowflake_secrets(parent)
    if passphrase:
        child["PRIVATE_KEY_PASSPHRASE"] = passphrase
    return child


def run_snow(
    argv: Sequence[str],
    *,
    config: Config,
    store: SecretStore | None = None,
    executable: str | None = None,
    popen: Callable[..., subprocess.Popen] | None = None,
) -> int:
    """Spawn snow with a child-only passphrase when Keychain has one for the connection."""
    snow = executable or shutil.which("snow")
    if not snow:
        raise ValueError("Install Snowflake CLI: uv tool install snowflake-cli")
    connection = parse_snow_connection(argv, config)
    secret_store = store or default_secret_store()
    passphrase = secret_store.get_passphrase(connection) if connection else None
    env = child_environment(os.environ, passphrase=passphrase)
    launcher = popen or subprocess.Popen
    process = launcher([snow, *argv], env=env)
    return process.wait()


def render_shell_wrapper(*, use_op: bool = False) -> str:
    lines = [
        "# Snowbeam snow wrapper — install in ~/.zshrc or ~/.bashrc",
        "# Normal `snow` usage; passphrases come from Keychain via snowbeam snow.",
        "snow() {",
        '  command snowbeam snow "$@"',
        "}",
    ]
    if use_op:
        lines.extend(
            [
                "",
                "# Optional 1Password (off by default): use op read in your own wrapper.",
                "# Snowbeam does not call op; add your own wrapper here if needed.",
            ]
        )
    return "\n".join(lines) + "\n"
