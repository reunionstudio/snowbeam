"""Access request import/apply and related governance helpers."""

from __future__ import annotations

from pathlib import Path

from .access_requests import AccessRequestPlanner, import_plan
from .starters import BUILTIN_STARTERS, list_starters


def add_requests_commands(commands):
    requests = commands.add_parser(
        "requests", help="Import YAML access requests into reviewed plans"
    )
    actions = requests.add_subparsers(dest="request_action")
    imp = actions.add_parser("import", help="Build a plan from a request file")
    imp.add_argument("file", type=Path)
    imp.add_argument("--template", required=True, help="Existing provisioning template")
    imp.add_argument("--json", action="store_true")
    apply = actions.add_parser("apply", help="Apply an approved access-request plan file")
    apply.add_argument("plan", type=Path)
    apply.add_argument("--approve", required=True)
    apply.add_argument("--json", action="store_true")

    accounts = commands.add_parser("accounts", help="Per-account Snowflake admin connections")
    account_actions = accounts.add_subparsers(dest="account_action")
    mark = account_actions.add_parser(
        "mark-admin", help="Mark the admin snow connection for an organization-account"
    )
    mark.add_argument("connection")
    mark.add_argument("--organization", required=True)
    mark.add_argument("--account", required=True)

    starters = commands.add_parser("starters", help="Role starter catalog")
    starter_actions = starters.add_subparsers(dest="starter_action")
    starter_actions.add_parser("list").add_argument("--json", action="store_true")
    save = starter_actions.add_parser("save", help="Save a custom additive starter")
    save.add_argument("name")
    save.add_argument("--title")
    save.add_argument("--description")
    save.add_argument("--grant", action="append", dest="grants", default=[])

    prefs = commands.add_parser("preferences", help="Snowbeam companion preferences")
    pref_actions = prefs.add_subparsers(dest="preference_action")
    airlock = pref_actions.add_parser("airlock-pack")
    airlock.add_argument("enabled", choices=["on", "off"])
    vault = pref_actions.add_parser(
        "vault-integration",
        help="Opt in to 1Password/Bitwarden CLI integration (off by default)",
    )
    vault.add_argument("enabled", choices=["on", "off"])


REQUEST_COMMANDS = {"requests", "accounts", "starters", "preferences"}


def run_requests(args, service):
    from .cli import emit

    fleet = service.fleet
    if args.command == "requests":
        if args.request_action == "import":
            result = import_plan(fleet, args.file, template=args.template)
        elif args.request_action == "apply":
            import json

            plan = json.loads(args.plan.read_text())
            result = AccessRequestPlanner(fleet).apply(plan, args.approve)
        else:
            raise ValueError("Use requests import or requests apply.")
    elif args.command == "accounts":
        if args.account_action != "mark-admin":
            raise ValueError("Use accounts mark-admin.")
        fleet.config.set_account_admin(args.organization, args.account, args.connection)
        result = {
            "organization": args.organization,
            "account": args.account,
            "admin_connection": args.connection,
        }
    elif args.command == "starters":
        if args.starter_action == "list":
            doc = fleet.config.read()
            catalog = list_starters(doc.get("preferences", {}), doc.get("starters", {}))
            result = {
                name: {k: v for k, v in spec.items() if k != "grants"}
                for name, spec in catalog.items()
            }
            if not getattr(args, "json", False):
                for name, spec in catalog.items():
                    origin = "builtin" if name in BUILTIN_STARTERS else "saved"
                    print(f"{name} ({origin}): {spec.get('title', '')}")
                return 0
        else:
            from .starters import validate_user_starter

            fleet.config.save(
                "starters",
                args.name,
                validate_user_starter(
                    args.name,
                    {
                        "title": args.title or args.name,
                        "description": args.description or "",
                        "grants": args.grants,
                    },
                ),
                create=True,
            )
            print(f"Starter {args.name}: saved.")
            return 0
    elif args.command == "preferences":
        if args.preference_action == "airlock-pack":
            enabled = args.enabled == "on"
            fleet.config.set_preference("airlock_pack", enabled)
            result = {"airlock_pack": enabled}
        elif args.preference_action == "vault-integration":
            enabled = args.enabled == "on"
            fleet.config.set_preference("vault_integration", enabled)
            result = {"vault_integration": enabled}
        else:
            raise ValueError("Use preferences airlock-pack or vault-integration.")
    else:
        raise ValueError("Unknown command.")
    emit(result)
    return 0
