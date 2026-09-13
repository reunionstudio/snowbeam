"""Snowbeam's human CLI and JSON surface for scripts and agents."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__
from .config import FIELDS, Config, ConfigError
from .desktop import install_launcher, install_reminders, notify_due, remove_reminders
from .labels import display_name
from .service import Service
from .snowflake import SnowClient, SnowError, safe_text
from .store import Store, expiry_label


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Snowflake accounts, connections, and token expirations."
    )
    root.add_argument("--version", action="version", version=f"Snowbeam {__version__}")
    root.add_argument("--state-dir", type=Path, help="Directory for the private metadata cache")
    root.add_argument(
        "--snow-config",
        type=Path,
        help="Snowflake CLI config.toml (uses standard discovery by default)",
    )
    root.add_argument("--snow-executable", help="Path to the Snowflake CLI executable")
    root.add_argument(
        "--demo",
        action="store_true",
        help="Use isolated synthetic data; never connect to Snowflake",
    )
    root.add_argument(
        "--fleet-config",
        type=Path,
        help="Snowbeam companion TOML (default: alongside Snowflake config)",
    )
    commands = root.add_subparsers(dest="command")
    from .fleet_cli import add_commands

    add_commands(commands)
    tui = commands.add_parser("tui", help="Open the terminal app (default)")
    tui.add_argument("--offline", action="store_true", help="Disable automatic metadata refresh")
    inventory = commands.add_parser(
        "inventory", help="Show cached organization and account inventory"
    )
    inventory.add_argument("--json", action="store_true")
    labels = commands.add_parser("labels", help="Read or edit local organization/account labels")
    labels.add_argument("kind", choices=["organization", "account"])
    labels.add_argument(
        "target", help="Organization identifier, organization-account, or account ID"
    )
    labels.add_argument("--alias", help="Friendly name; an empty string clears it")
    labels.add_argument(
        "--notes", help="Local notes; an empty string clears them. Never enter secrets."
    )
    labels.add_argument("--json", action="store_true")
    tokens = commands.add_parser("tokens", help="Show cached PAT metadata")
    tokens.add_argument("--json", action="store_true")
    refresh = commands.add_parser(
        "refresh", help="Verify connections and refresh current-user PAT metadata"
    )
    refresh.add_argument("name", nargs="?")
    refresh.add_argument(
        "--organization",
        action="store_true",
        help="Also discover organization accounts (requires privileges)",
    )
    refresh.add_argument(
        "--non-interactive", action="store_true", help="Skip browser/password authentication"
    )
    refresh.add_argument("--json", action="store_true")
    check = commands.add_parser("check", help="Flag cached expirations and incomplete verification")
    check.add_argument(
        "--refresh",
        action="store_true",
        help="First refresh PAT/key-pair connections without interactive sign-in",
    )
    check.add_argument("--notify", action="store_true", help="Send deduplicated desktop reminders")
    check.add_argument(
        "--days", type=int, default=14, help="Upcoming expiration window (default: 14)"
    )
    check.add_argument("--json", action="store_true")
    connections = commands.add_parser(
        "connections", help="Manage local Snowflake connection profiles"
    )
    actions = connections.add_subparsers(dest="connection_action")
    listing = actions.add_parser("list")
    listing.add_argument("--json", action="store_true")
    for action in ("add", "edit"):
        edit = actions.add_parser(action)
        edit.add_argument("name")
        for field in FIELDS:
            edit.add_argument("--" + field.replace("_", "-"))
    for action in ("default", "remove"):
        sub = actions.add_parser(action)
        sub.add_argument("name")
        if action == "remove":
            sub.add_argument(
                "--yes", action="store_true", help="Confirm removal of the local profile"
            )
    binding = actions.add_parser(
        "bind-token", help="Record a PAT association; never change credential values"
    )
    binding.add_argument("name")
    binding.add_argument("token", help="Token name; use an empty string to remove the association")
    desktop = commands.add_parser("desktop", help="Install the Linux/Omarchy app launcher")
    desktop.add_argument("action", choices=["install"])
    reminders = commands.add_parser(
        "reminders", help="Install or remove opt-in desktop expiry checks"
    )
    reminders.add_argument("action", choices=["install", "remove"])
    updates = commands.add_parser("updates", help="Check releases and show upgrade instructions")
    updates.add_argument(
        "action", nargs="?", choices=["status", "check", "enable", "disable"], default="status"
    )
    updates.add_argument("--json", action="store_true")
    policy = commands.add_parser("refresh-policy", help="View or set background refresh intervals")
    policy.add_argument("name", nargs="?", help="Connection name")
    policy.add_argument("--minutes", type=int, help="1–1440 minutes; 0 disables background refresh")
    policy.add_argument("--json", action="store_true")
    return root


def print_table(title: str, columns: tuple[str, ...], rows: list[tuple]) -> None:
    table = Table(title=title, show_lines=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        from rich.text import Text

        table.add_row(*(Text(safe_text(value)) for value in row))
    Console().print(table)


def emit(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))


def run(args, service: Service) -> int:
    from .fleet_cli import COMMANDS
    from .fleet_cli import run as run_fleet

    if getattr(args, "fleet_config", None) and not service.demo:
        from .fleet_service import FleetService

        service.fleet = FleetService(service, args.fleet_config)
    if args.command in COMMANDS:
        return run_fleet(args, service)
    if args.command == "labels":
        key = args.target
        if args.kind == "account":
            matches = [
                a
                for a in service.store.accounts()
                if key in (a["id"], f"{a['organization']}-{a['name']}")
            ]
            if len(matches) != 1:
                raise ValueError(
                    "Choose one known account by its organization-account identifier or "
                    "its exact id from inventory --json."
                )
            key = matches[0]["id"]
        if args.alias is not None or args.notes is not None:
            service.store.set_labels(args.kind, key, alias=args.alias, notes=args.notes)
        record = service.store.labels(args.kind, key)
        if args.json:
            emit(record)
        else:
            print(safe_text(display_name(record["identifier"], record["alias"])))
            for line in record["notes"].splitlines():
                print(safe_text(line))
        return 0
    if args.command == "updates":
        from .installation import upgrade_instructions
        from .updates import Updates, describe

        updates = Updates(service.store.directory)
        if args.action in ("check", "enable") and service.demo:
            raise ValueError("Online update checks are unavailable in demo mode.")
        if args.action in ("enable", "disable"):
            service.preferences.set_automatic_updates(args.action == "enable")
        status = updates.check() if args.action == "check" else updates.status()
        status["automatic"] = service.preferences.automatic_updates()
        status["installation"], status["upgrade_instructions"] = upgrade_instructions()
        if args.json:
            emit(status)
        else:
            print(describe(status))
            print(f"Automatic checks: {'on' if status['automatic'] else 'off'}")
            print(status["upgrade_instructions"])
        return 2 if args.action == "check" and status["error"] else 0
    if args.command == "refresh-policy":
        if args.minutes is not None:
            if not args.name:
                raise ValueError("Specify a connection name when setting its refresh interval.")
            service.preferences.set_refresh_minutes(
                service.config.profile(args.name).key, args.minutes
            )
        profiles = [service.config.profile(args.name)] if args.name else service.config.profiles()
        rows = [
            {
                "connection": p.name,
                "minutes": service.preferences.refresh_minutes(p.key),
                "unattended_authentication": p.background_safe,
            }
            for p in profiles
        ]
        if args.json:
            emit(rows)
        else:
            for row in rows:
                cadence = f"every {row['minutes']} minutes" if row["minutes"] else "disabled"
                print(f"{safe_text(row['connection'])}: {cadence}")
        return 0
    if args.command in (None, "tui"):
        if not sys.stdout.isatty():
            raise ValueError(
                "The app needs a terminal. Use inventory --json or "
                "--demo check --json for headless output."
            )
        from .tui import Snowbeam
        from .upgrader import Restart, restart_app

        result = Snowbeam(service, auto_refresh=not getattr(args, "offline", False)).run()
        if isinstance(result, Restart):
            restart_app(result.command, args, service)
        return 0
    if args.command in ("desktop", "reminders"):
        if service.demo:
            raise ValueError("Desktop integration is unavailable in demo mode.")
        if args.command == "desktop":
            print(f"Launcher installed: {install_launcher(service)}")
        elif args.action == "install":
            for path in install_reminders(service):
                print(f"Reminder installed: {path}")
        else:
            remove_reminders()
            print("Snowbeam desktop reminders removed.")
        return 0
    if args.command == "check":
        if args.days < 1:
            raise ValueError("--days must be a positive integer.")
        issues = []
        try:
            service.import_profiles()
            if args.refresh:
                issues.extend(service.refresh(interactive=False, scheduled=True).issues)
        except (ConfigError, OSError) as exc:
            issues.append(str(exc))
        alerts = service.store.alerts(days=args.days)
        issues = list(dict.fromkeys(issues + service.coverage_issues() + service.fleet.issues()))
        sent = failed = 0
        if args.notify:
            if service.demo:
                raise ValueError("Desktop notifications are unavailable in demo mode.")
            sent, failed = notify_due(service.store, alerts, issues)
            if failed:
                issues.append(
                    "Desktop notification delivery failed; reminders were not marked as sent."
                )
        if args.json:
            emit({"alerts": alerts, "verification_issues": issues, "notifications_sent": sent})
        else:
            print_table(
                "Token attention",
                ("Account", "User", "Token", "Expiry", "Status", "Last verified"),
                [
                    (
                        f"{a['organization']}-{a['account_name']}",
                        a["user_name"],
                        a["name"],
                        a["expiry_label"],
                        a["status"],
                        a["checked_at"],
                    )
                    for a in alerts
                ],
            )
            for issue in issues:
                print("Verification: " + safe_text(issue))
            if not alerts and not issues:
                print(f"No expirations within {args.days} days in the verified inventory.")
        return 2 if issues else 1 if alerts else 0
    service.import_profiles()
    if args.command == "refresh":
        result = service.refresh(
            args.name, interactive=not args.non_interactive, organization=args.organization
        )
        if args.json:
            emit({"refreshed": result.refreshed, "issues": result.issues})
        else:
            print(f"Refreshed {result.refreshed} connection(s).")
            for issue in result.issues:
                print(safe_text(issue))
        return 2 if result.issues else 0
    if args.command == "connections":
        action = args.connection_action or "list"
        if action in ("add", "edit"):
            settings = {
                field: getattr(args, field) for field in FIELDS if getattr(args, field) is not None
            }
            if action == "add":
                settings.setdefault("authenticator", "externalbrowser")
            service.config.save(args.name, settings, create=action == "add")
        elif action == "default":
            service.config.set_default(args.name)
        elif action == "remove":
            if not args.yes:
                raise ValueError("Use --yes to confirm removal of this local connection profile.")
            service.config.remove(args.name)
        elif action == "bind-token":
            service.store.bind_token(service.config.profile(args.name).key, args.token or None)
        if action != "list":
            service.import_profiles()
            print(f"Connection {args.name!r}: {action} complete.")
            return 0
        rows = service.store.connections()
        if getattr(args, "json", False):
            emit(rows)
        else:
            print_table(
                "Connections",
                ("Name", "Account", "User", "Auth", "Default", "Health"),
                [
                    (
                        row["name"],
                        row["settings"].get("account"),
                        row["settings"].get("user"),
                        row["settings"].get("authenticator", "snowflake"),
                        bool(row["is_default"]),
                        row["status"],
                    )
                    for row in rows
                ],
            )
        return 0
    if args.command == "tokens":
        rows = service.store.tokens()
        if args.json:
            emit(rows)
        else:
            print_table(
                "Cached PAT inventory",
                ("Account", "User", "Token", "Status", "Expiration", "Remaining"),
                [
                    (
                        row["account_name"],
                        row["user_name"],
                        row["name"],
                        row["status"],
                        row["expires_at"],
                        expiry_label(row["expires_at"]),
                    )
                    for row in rows
                ],
            )
        return 0
    accounts = service.store.accounts()
    if args.json:
        emit(
            {
                "accounts": accounts,
                "connections": service.store.connections(),
                "tokens": service.store.tokens(),
                "verification_issues": service.coverage_issues(),
                "identities": service.fleet.identities(),
            }
        )
    else:
        print_table(
            "Accounts · visibility depends on your privileges",
            ("Organization", "Account", "Locator", "Region", "Last verified"),
            [
                (
                    display_name(a["organization"], a["organization_alias"]),
                    display_name(a["name"], a["account_alias"]),
                    a["locator"],
                    a["region"],
                    a["checked_at"],
                )
                for a in accounts
            ],
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.demo:
            from .demo import demo_service

            with tempfile.TemporaryDirectory(prefix="snowbeam-demo-") as directory:
                return run(args, demo_service(Path(directory)))
        return run(
            args,
            Service(
                Config(args.snow_config), Store(args.state_dir), SnowClient(args.snow_executable)
            ),
        )
    except (ConfigError, SnowError, ValueError, OSError, sqlite3.Error) as exc:
        print("Snowbeam: " + safe_text(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
