"""Small commands usable by both a person and an AI operating Snowbeam."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .fleet import LABEL_FIELDS, REFERENCE_FIELDS, TEMPLATE_FIELDS, WORKLOAD_FIELDS
from .provision import Provisioner

COMMANDS = {"templates", "identities", "credentials", "operations", "exec", "runtime"}


def add_commands(commands):
    templates = commands.add_parser(
        "templates", help="Reusable, account-bound agent setup templates"
    )
    actions = templates.add_subparsers(dest="template_action")
    actions.add_parser("list").add_argument("--json", action="store_true")
    for action in ("add", "edit"):
        sub = actions.add_parser(action)
        sub.add_argument("name")
        for field in sorted(TEMPLATE_FIELDS):
            sub.add_argument(
                "--" + field.replace("_", "-"), type=int if field == "days_to_expiry" else str
            )
    identities = commands.add_parser("identities", help="Consultant and agent identity inventory")
    actions = identities.add_subparsers(dest="identity_action")
    listing = actions.add_parser("list")
    listing.add_argument("--client")
    listing.add_argument("--json", action="store_true")
    for action in ("track", "add", "label"):
        sub = actions.add_parser(action)
        sub.add_argument("name")
        if action == "track":
            sub.add_argument("--connection", required=True)
            sub.add_argument("--kind", choices=["person", "agent"], default="person")
        if action == "add":
            sub.add_argument("--template", required=True)
            sub.add_argument("--user", required=True)
            for field in sorted(WORKLOAD_FIELDS):
                sub.add_argument("--" + field.replace("_", "-"))
            sub.add_argument("--connection", help="Defaults to the identity name")
        for field in sorted(LABEL_FIELDS):
            sub.add_argument("--" + field)
    show = actions.add_parser("show")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    refresh = actions.add_parser("refresh")
    refresh.add_argument("name", nargs="?")
    refresh.add_argument(
        "--history",
        action="store_true",
        help="Also inspect the latest user login (privilege dependent, delayed)",
    )
    refresh.add_argument("--json", action="store_true")
    for action in ("plan", "apply"):
        sub = actions.add_parser(action)
        sub.add_argument("name")
        sub.add_argument(
            "--action", choices=["provision", "rotate", "retire-old", "revoke"], default="provision"
        )
        sub.add_argument("--json", action="store_true")
        if action == "apply":
            sub.add_argument("--approve", required=True, help="Approval digest from a fresh plan")
    verify = actions.add_parser("verify")
    verify.add_argument("name")
    verify.add_argument("--runtime", required=True, help="Declared runtime; run this command there")
    verify.add_argument("--json", action="store_true")
    receipt = actions.add_parser(
        "accept-test", help="Review and accept a trusted runtime's test report"
    )
    receipt.add_argument("name")
    receipt.add_argument("receipt", type=Path)
    receipt.add_argument("--approve", help="Digest from the report review; omit to review only")
    runtime = commands.add_parser("runtime", help="Export and test one agent on its own runtime")
    runtime_actions = runtime.add_subparsers(dest="runtime_action")
    export = runtime_actions.add_parser("export")
    export.add_argument("name")
    export.add_argument("--out", required=True, type=Path)
    test = runtime_actions.add_parser("test")
    test.add_argument("bundle", type=Path)
    test.add_argument("--out", required=True, type=Path)
    child = runtime_actions.add_parser("exec")
    child.add_argument("bundle", type=Path)
    child.add_argument("child", nargs=argparse.REMAINDER)
    credentials = commands.add_parser(
        "credentials", help="Bind vault references without copying secrets"
    )
    actions = credentials.add_subparsers(dest="credential_action")
    bind = actions.add_parser("bind")
    bind.add_argument("name")
    for field in sorted(REFERENCE_FIELDS):
        bind.add_argument("--" + field.replace("_", "-"), required=field in {"provider", "item"})
    operations = commands.add_parser(
        "operations", help="Recent lifecycle operations and partial failures"
    )
    operations.add_argument("--identity")
    operations.add_argument("--json", action="store_true")
    run = commands.add_parser(
        "exec", help="Run a child command with one identity's vault credential"
    )
    run.add_argument("name")
    run.add_argument("child", nargs=argparse.REMAINDER)


def run(args, service):
    from .cli import emit, print_table

    fleet = service.fleet
    if args.command != "runtime" or args.runtime_action == "export":
        service.import_profiles()
    result = None
    if args.command == "templates":
        action = args.template_action or "list"
        if action == "list":
            result = fleet.config.read()["templates"]
        else:
            values = {f: getattr(args, f) for f in TEMPLATE_FIELDS if getattr(args, f) is not None}
            fleet.config.save("templates", args.name, values, create=action == "add")
            print(f"Template {args.name}: saved. No Snowflake objects changed.")
            return 0
    elif args.command == "identities":
        action = args.identity_action or "list"
        if action == "list":
            result = fleet.identities(client=getattr(args, "client", None))
            if not getattr(args, "json", False):
                print_table(
                    "Identities · access evidence and local intent",
                    ("Identity", "Client", "Account", "User", "Runtime", "Status"),
                    [
                        (
                            r["id"],
                            r["client"],
                            f"{r['organization'] or '?'} / {r['account_name'] or '?'}",
                            r["user_name"],
                            r["runtime"],
                            r["status"],
                        )
                        for r in result
                    ],
                )
                return 0
        elif action in {"add", "track", "label"}:
            labels = {f: getattr(args, f) for f in LABEL_FIELDS if getattr(args, f) is not None}
            if action == "add":
                if not labels.get("owner") or not labels.get("runtime"):
                    raise ValueError("An agent needs an owner and declared runtime.")
                labels.update(
                    {f: getattr(args, f) for f in WORKLOAD_FIELDS if getattr(args, f) is not None}
                )
                fleet.config.add_agent(
                    args.name, args.template, args.user, args.connection or args.name, **labels
                )
            elif action == "track":
                service.config.profile(args.connection)
                fleet.config.track(args.name, args.connection, kind=args.kind, **labels)
            else:
                fleet.config.spec(args.name)
                fleet.config.save("identities", args.name, labels)
            print(f"Identity {args.name}: saved. No Snowflake objects changed.")
            return 0
        elif action == "show":
            matches = [
                r
                for r in fleet.identities()
                if r["id"] == args.name or r["connection"] == args.name
            ]
            if len(matches) != 1:
                raise ValueError("Select one identity or connection.")
            result = matches[0]
        elif action == "refresh":
            result = fleet.refresh(args.name, history=args.history)
            emit(result)
            return 2 if result["issues"] else 0
        elif action == "accept-test":
            from .runtime import accept_receipt, review_receipt

            result = (
                accept_receipt(fleet, args.name, args.receipt, args.approve)
                if args.approve
                else review_receipt(fleet, args.name, args.receipt)
            )
        elif action == "plan":
            result = Provisioner(fleet).plan(args.name, args.action)
        elif action == "apply":
            result = Provisioner(fleet).apply(args.name, args.action, args.approve)
        elif action == "verify":
            result = Provisioner(fleet).verify(args.name, args.runtime)
    elif args.command == "credentials":
        if args.credential_action != "bind":
            raise ValueError("Use credentials bind to record an existing vault item reference.")
        spec = fleet.config.spec(args.name)
        if fleet.state(args.name):
            raise ValueError(
                "Use the managed credential lifecycle for a provisioned agent; its "
                "reference cannot be replaced independently."
            )
        reference = {f: getattr(args, f) for f in REFERENCE_FIELDS if getattr(args, f) is not None}
        fleet.config.save("credentials", args.name, reference)
        result = {
            "identity": spec["id"],
            "credential": reference,
            "status": "reference_saved_not_verified",
        }
    elif args.command == "runtime":
        if service.demo:
            raise ValueError("Runtime handoff is unavailable in demo mode.")
        from .runtime import export_bundle, load_bundle, session, test_bundle, write_json

        if args.runtime_action == "export":
            bundle = export_bundle(fleet, args.name)
            write_json(args.out, bundle)
            result = {
                "path": str(args.out),
                "identity": args.name,
                "contains": "One connection and credential references; no admin connection or "
                "credential values.",
            }
        elif args.runtime_action == "test":
            result = test_bundle(load_bundle(args.bundle), service.client)
            write_json(args.out, result)
        elif args.runtime_action == "exec":
            child = args.child[1:] if args.child and args.child[0] == "--" else args.child
            if not child:
                raise ValueError("Supply a child command after --.")
            with session(load_bundle(args.bundle)) as actual:
                return subprocess.call(child, env=actual.environment)
        else:
            raise ValueError("Use runtime export, test, or exec.")
    elif args.command == "operations":
        result = service.store.operations(fleet.key(args.identity) if args.identity else None)
    elif args.command == "exec":
        if service.demo:
            raise ValueError("External commands are unavailable in demo mode.")
        child = args.child[1:] if args.child and args.child[0] == "--" else args.child
        if not child:
            raise ValueError(
                "Supply a command after --, for example: -- snow sql -c sam -q 'select "
                "current_user()'"
            )
        spec = fleet.config.spec(args.name)
        reference = spec.get("credential")
        if not reference and spec.get("auth") != "WIF":
            raise ValueError(
                "This identity has no vault reference. Use its standard connection directly."
            )
        if fleet.state(args.name).get("status") == "revoked":
            raise ValueError("This identity was revoked.")
        profile = service.config.profile(spec["connection"], environment=False)
        with fleet.session_for(profile) as actual:
            return subprocess.call(child, env=actual.environment)
    emit(result)
    return 0
