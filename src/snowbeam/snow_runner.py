"""Run the Snowflake CLI with control-plane connection views and admin unlock."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from platformdirs import user_data_path

from .admin_challenge import (
    AdminAccessDenied,
    AdminLockoutStore,
    apply_decoy_to_child_env,
    unlock_admin_passphrase,
)
from .config import Config
from .connection_access import ConnectionAccess
from .os_secrets import SecretStore, default_secret_store
from .run_context import RunContext, resolve_run_context
from .snow_views import config_for_run

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
    snowflake_home: Path | None = None,
) -> dict[str, str]:
    child = strip_snowflake_secrets(parent)
    if snowflake_home is not None:
        child["SNOWFLAKE_HOME"] = str(snowflake_home)
    if passphrase:
        child["PRIVATE_KEY_PASSPHRASE"] = passphrase
    return child


def _deny_admin_access(context: RunContext, connection: str) -> None:
    if context.silent_admin_denial:
        raise AdminAccessDenied()
    raise AdminAccessDenied()


def resolve_passphrase(
    connection: str | None,
    *,
    master_config: Config,
    state_dir: Path,
    context: RunContext,
    access: ConnectionAccess,
    secret_store: SecretStore,
    lockout: AdminLockoutStore,
) -> str | None:
    if not connection:
        return None
    if not access.connection_allowed(connection, agent_context=context.agent_context):
        _deny_admin_access(context, connection)

    needs_admin = access.requires_admin_challenge(connection)
    profile_settings = master_config.profile(connection, environment=False).settings
    uses_key = bool(profile_settings.get("private_key_file"))

    if needs_admin and context.agent_context:
        _deny_admin_access(context, connection)

    if needs_admin and uses_key:
        return unlock_admin_passphrase(
            connection,
            secret_store,
            lockout,
            silent_on_denial=context.agent_context,
        )

    if needs_admin:
        return None

    return secret_store.get_passphrase(connection)


def run_snow(
    argv: Sequence[str],
    *,
    config: Config,
    state_dir: Path | None = None,
    store: SecretStore | None = None,
    executable: str | None = None,
    popen: Callable[..., subprocess.Popen] | None = None,
    agent_id: str | None = None,
    agent_proof: str | None = None,
    environ: dict[str, str] | None = None,
) -> int:
    """Spawn snow with a filtered config view and child-only secrets when allowed."""
    snow = executable or shutil.which("snow")
    if not snow:
        raise ValueError("Install Snowflake CLI: uv tool install snowflake-cli")

    parent_env = environ if environ is not None else os.environ
    data_dir = state_dir or Path(
        parent_env.get(
            "SNOWBEAM_STATE_DIR",
            str(user_data_path("snowbeam", appauthor=False)),
        )
    )
    access = ConnectionAccess(data_dir)
    context = resolve_run_context(
        data_dir, agent_id=agent_id, agent_proof=agent_proof, environ=parent_env
    )
    if context.agent_context and context.agent_id is None:
        return AdminAccessDenied.exit_code
    master = config
    view_config = config_for_run(master, access, data_dir, agent_context=context.agent_context)
    connection = parse_snow_connection(argv, view_config)
    try:
        if connection and not access.connection_allowed(
            connection, agent_context=context.agent_context
        ):
            _deny_admin_access(context, connection)
        secret_store = store or default_secret_store()
        lockout = AdminLockoutStore(data_dir)
        passphrase = resolve_passphrase(
            connection,
            master_config=master,
            state_dir=data_dir,
            context=context,
            access=access,
            secret_store=secret_store,
            lockout=lockout,
        )
    except AdminAccessDenied:
        return AdminAccessDenied.exit_code

    snow_home = view_config.path.parent
    env = child_environment(parent_env, passphrase=passphrase, snowflake_home=snow_home)
    if (
        connection
        and access.requires_admin_challenge(connection)
        and not passphrase
        and context.agent_context
    ):
        apply_decoy_to_child_env(env, connection)

    launcher = popen or subprocess.Popen
    process = launcher([snow, *argv], env=env)
    return process.wait()


def render_shell_wrapper(*, use_op: bool = False) -> str:
    lines = [
        "# Snowbeam snow wrapper — install in ~/.zshrc or ~/.bashrc",
        "# Normal `snow` usage; admin passphrases stay out of the parent environment.",
        "snow() {",
        '  command snowbeam snow "$@"',
        "}",
        "",
        "# Optional decoy so agents never see a real passphrase in the environment:",
        '# export PRIVATE_KEY_PASSPHRASE="decoy-replace-me"',
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
