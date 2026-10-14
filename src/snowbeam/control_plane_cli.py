"""CLI for connection classification and agent identities."""

from __future__ import annotations

import json

from .agent_registry import ENV_AGENT_ID, ENV_AGENT_PROOF, AgentRegistry
from .config import Config
from .connection_access import CLASS_ADMIN, CLASS_AGENT, ConnectionAccess


def _emit(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))


def add_access_commands(connections_sub) -> None:
    classify = connections_sub.add_parser(
        "classify",
        help="Mark a connection as admin (human) or agent-scoped",
    )
    classify.add_argument("name", help="Snowflake connection name")
    classify.add_argument(
        "--role",
        required=True,
        choices=[CLASS_ADMIN, CLASS_AGENT],
        help="admin: human challenge required; agent: visible to registered agents",
    )
    classify.add_argument("--json", action="store_true")
    unclassify = connections_sub.add_parser(
        "unclassify",
        help="Remove explicit classification (defaults to admin)",
    )
    unclassify.add_argument("name")
    unclassify.add_argument("--json", action="store_true")


def run_access_command(args, service) -> int:
    access = ConnectionAccess(service.store.directory)
    service.config.profile(args.name, environment=False)
    if args.connection_action == "classify":
        access.set_classification(args.name, args.role)
        payload = {"connection": args.name, "role": args.role}
    else:
        access.remove_classification(args.name)
        payload = {"connection": args.name, "role": CLASS_ADMIN, "explicit": False}
    if args.json:
        _emit(payload)
    else:
        print(f"Connection {args.name!r}: {payload['role']}")
    return 0


def add_agent_commands(commands) -> None:
    agents = commands.add_parser(
        "agents",
        help="Register agent identities for scoped snowbeam snow access",
    )
    sub = agents.add_subparsers(dest="agents_action")
    for name, help_text in (
        ("list", "List registered agents"),
        ("plan-register", "Show digest for registering an agent"),
        ("register", "Register an agent after review or confirmation"),
        ("plan-revoke", "Show digest for revoking an agent"),
        ("revoke", "Revoke an agent identity"),
        ("proof", "Mint a short-lived proof for SNOWBEAM_AGENT_PROOF"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        if name != "list":
            cmd.add_argument("agent_id")
        if name in {"register", "revoke"}:
            cmd.add_argument(
                "--approve",
                dest="approve_digest",
                help="Approval digest from plan-register or plan-revoke",
            )
        cmd.add_argument("--json", action="store_true")


def run_agents_command(args, service) -> int:
    registry = AgentRegistry(service.store.directory)
    action = args.agents_action
    if not action:
        raise ValueError(
            "Choose an agents action: list, plan-register, register, plan-revoke, revoke, proof."
        )
    if action == "list":
        rows = [
            {
                "agent_id": row.agent_id,
                "fingerprint": row.secret_fingerprint,
                "created_at": row.created_at,
                "revoked": row.revoked,
            }
            for row in registry.list_agents()
        ]
        if args.json:
            _emit(rows)
        else:
            for row in rows:
                status = "revoked" if row["revoked"] else "active"
                print(f"{row['agent_id']}: {status} (fp {row['fingerprint']})")
        return 0
    agent_id = args.agent_id
    if action == "plan-register":
        plan = registry.plan_register(agent_id)
        if args.json:
            _emit(plan)
        else:
            print(f"Register {agent_id}: approve {plan['digest']}")
        return 0
    if action == "register":
        record = registry.confirm_register(agent_id, approve_digest=args.approve_digest)
        payload = {
            "agent_id": record.agent_id,
            "fingerprint": record.secret_fingerprint,
            "env": {ENV_AGENT_ID: record.agent_id},
        }
        if args.json:
            _emit(payload)
        else:
            print(f"Registered agent {record.agent_id!r}.")
            print(f"Run: snowbeam agents proof {record.agent_id}")
        return 0
    if action == "plan-revoke":
        plan = registry.plan_revoke(agent_id)
        if args.json:
            _emit(plan)
        else:
            print(f"Revoke {agent_id}: approve {plan['digest']}")
        return 0
    if action == "revoke":
        registry.confirm_revoke(agent_id, approve_digest=args.approve_digest)
        if args.json:
            _emit({"agent_id": agent_id, "revoked": True})
        else:
            print(f"Revoked agent {agent_id!r}.")
        return 0
    if action == "proof":
        proof = registry.mint_proof(agent_id)
        payload = {
            "agent_id": agent_id,
            ENV_AGENT_ID: agent_id,
            ENV_AGENT_PROOF: proof,
        }
        if args.json:
            _emit(payload)
        else:
            print(f"export {ENV_AGENT_ID}={agent_id}")
            print(f"export {ENV_AGENT_PROOF}={proof}")
        return 0
    raise ValueError(f"Unknown agents action {action!r}.")


def filter_connection_rows(rows: list[dict], config: Config, state_dir, *, agent_context: bool):
    access = ConnectionAccess(state_dir)
    names = set(
        access.visible_connection_names(
            [row["name"] for row in rows],
            agent_context=agent_context,
        )
    )
    enriched = []
    for row in rows:
        if row["name"] not in names:
            continue
        role = access.classification(row["name"])
        enriched.append({**row, "access_role": role})
    return enriched
