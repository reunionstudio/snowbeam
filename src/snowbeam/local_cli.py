"""CLI handlers for local credential hygiene and snowbeam snow."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from rich.console import Console
from rich.table import Table

from .config import Config
from .local_scan import scan_exit_code, scan_local_machine
from .os_secrets import (
    default_secret_store,
    keychain_add_command,
    keychain_service,
)
from .snow_runner import render_shell_wrapper, run_snow
from .snowflake import safe_text


def _emit(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))


def run_local_scan(args, config: Config) -> int:
    report = scan_local_machine(config)
    if args.json:
        _emit(report)
    else:
        if report["clean"]:
            print("No local Snowflake credential hygiene issues found.")
        else:
            table = Table(title="Local credential hygiene", show_lines=False)
            table.add_column("Severity")
            table.add_column("Check")
            table.add_column("Location")
            table.add_column("Message")
            table.add_column("Suggested fix")
            for row in report["findings"]:
                table.add_row(
                    row["severity"],
                    row["check"],
                    safe_text(row["location"]),
                    safe_text(row["message"]),
                    safe_text(row["fix"]),
                )
            Console().print(table)
    return scan_exit_code(report)


def run_local_wrapper(args) -> int:
    text = render_shell_wrapper(use_op=args.op)
    sys.stdout.write(text)
    return 0


def run_snow_command(args, config: Config, executable: str | None) -> int:
    return run_snow(args.snow_args, config=config, executable=executable)


def run_secrets(args, config: Config) -> int:
    store = default_secret_store()
    action = args.secrets_action
    if action == "set-passphrase":
        config.profile(args.connection)
        first = getpass.getpass(f"Passphrase for connection {args.connection}: ")
        if not first:
            raise ValueError("An empty passphrase was not stored.")
        second = getpass.getpass("Passphrase (again): ")
        if first != second:
            raise ValueError("Passphrases did not match.")
        store.set_passphrase(args.connection, first)
        if not args.json:
            print(f"Stored Keychain entry for connection {args.connection!r}.")
            print("One-time manual alternative (prompts for the passphrase):")
            print(keychain_add_command(args.connection))
        else:
            _emit(
                {
                    "connection": args.connection,
                    "service": keychain_service(args.connection),
                    "stored": True,
                    "manual_command": keychain_add_command(args.connection),
                }
            )
        return 0
    if action == "status":
        if args.connection:
            names = [args.connection]
        else:
            names = [profile.name for profile in config.profiles(environment=False)]
        rows = [store.status(name) for name in names]
        if args.json:
            _emit(rows if not args.connection else rows[0])
        else:
            for row in rows:
                state = "stored" if row["stored"] else "not stored"
                print(f"{row['connection']}: {state} (service {row['service']})")
        return 0
    if action == "remove":
        store.delete_passphrase(args.connection)
        if args.json:
            _emit({"connection": args.connection, "removed": True})
        else:
            print(f"Removed Keychain entry for connection {args.connection!r}.")
        return 0
    raise ValueError("Unknown secrets action.")


def add_local_commands(commands) -> None:
    local = commands.add_parser(
        "local", help="Read-only Snowflake credential hygiene on this machine"
    )
    local_sub = local.add_subparsers(dest="local_action")
    scan = local_sub.add_parser("scan", help="Scan configs and environment (no Snowflake calls)")
    scan.add_argument("--json", action="store_true")
    wrapper = local_sub.add_parser(
        "keychain-wrapper",
        help="Print a shell function that delegates to snowbeam snow",
    )
    wrapper.add_argument(
        "--op",
        action="store_true",
        help="Include optional 1Password notes (Snowbeam still does not call op)",
    )


def add_snow_command(commands) -> None:
    snow = commands.add_parser(
        "snow",
        help="Run the Snowflake CLI with a child-only PRIVATE_KEY_PASSPHRASE when stored",
    )
    snow.add_argument("snow_args", nargs=argparse.REMAINDER)


def add_secrets_commands(commands) -> None:
    secrets = commands.add_parser(
        "secrets",
        help="Store Snowflake key passphrases in the OS secret service (not 1Password/Bitwarden)",
    )
    secrets_sub = secrets.add_subparsers(dest="secrets_action")
    set_pass = secrets_sub.add_parser(
        "set-passphrase", help="Prompt once and store a connection passphrase in Keychain"
    )
    set_pass.add_argument("connection", help="Snowflake connection name from connections.toml")
    set_pass.add_argument("--json", action="store_true")
    status = secrets_sub.add_parser("status", help="Show whether passphrases are stored")
    status.add_argument("connection", nargs="?", help="Omit to list every connection")
    status.add_argument("--json", action="store_true")
    remove = secrets_sub.add_parser("remove", help="Delete a stored passphrase")
    remove.add_argument("connection")
    remove.add_argument("--json", action="store_true")
