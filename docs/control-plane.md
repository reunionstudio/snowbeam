# Snowbeam control plane

Snowbeam is an **inventory and control plane** for Snowflake connections and
identities on your machine. It is **not** a passphrase vault that agents can
borrow silently. Humans keep admin paths; autonomous agents use explicitly
scoped connections and proofs.

Snowbeam does not depend on Airlock.

## Connection classes

Each Snowflake connection name can be marked:

| Role | Meaning |
|------|---------|
| `admin` | Human consulting connections (default when unlisted — **fail closed** for agents) |
| `agent` | Scoped Snowflake users for autonomous agents |

```sh
snowbeam connections classify work --role admin
snowbeam connections classify agent-sam --role agent
snowbeam connections unclassify work   # back to implicit admin
snowbeam connections list              # Role column; filtered in agent context
```

Metadata lives in Snowbeam state (`connection_access.toml`), not in
`connections.toml`, so Snowflake CLI files stay standard.

## Separate snow config views

The Snowflake CLI reads one config tree per process (`SNOWFLAKE_HOME` or
`--config-file`). Snowbeam materializes filtered views under state:

- `snow-views/admin/` — all connections (human `snowbeam snow`)
- `snow-views/agent/` — only `agent`-classified connections

`snowbeam snow` sets `SNOWFLAKE_HOME` on the child `snow` process to the
appropriate view.

## Admin unlock (human only)

Admin connections with private keys require an **interactive TTY** challenge
before Snowbeam injects a real `PRIVATE_KEY_PASSPHRASE` into the child process.

- The real passphrase is **never** left in the parent environment.
- Optional **decoy** `PRIVATE_KEY_PASSPHRASE` in the shell can mislead agents;
  Snowbeam strips it and only injects the real value after unlock.
- Passphrases for admin connections are stored with `snowbeam secrets
  set-passphrase` (Keychain / secret service).
- Wrong attempts are **rate limited** (lockout after two failures, escalating
  backoff) in `admin_lockout.json` (HMAC-protected, mode `0600`).

**Silent failure for agents:** When an authenticated agent (or invalid proof)
targets an admin connection, `snowbeam snow` exits with a generic failure code
and **does not** explain lockout, decoys, or classification.

**Tradeoff:** Silent failure hides misuse from agents but also gives humans no
hint when a script runs without a TTY. Run admin `snow` from an interactive
terminal.

### macOS Keychain and Touch ID

On macOS, passphrases remain in Keychain (`snowbeam.<connection>`). After
Keychain retrieval, humans confirm on `/dev/tty` (typed passphrase must match).
**LocalAuthentication / Touch ID gating** for Keychain items is not wired yet;
use `security add-generic-password` access controls manually if you need
user-presence prompts before `security` returns a value. Linux uses
`secret-tool` or typed fallback.

## Agent authentication

Each agent identity has a random key file and short-lived proofs:

```sh
snowbeam agents plan-register sam
snowbeam agents register sam --approve DIGEST   # or confirm on a TTY
snowbeam agents proof sam                         # export env vars (~5 min)
export SNOWBEAM_AGENT_ID=...
export SNOWBEAM_AGENT_PROOF=...
snowbeam snow connection test -c agent-sam
```

Revocation is governance:

```sh
snowbeam agents plan-revoke sam
snowbeam agents revoke sam --approve DIGEST
```

Agent connections skip the admin challenge and use their own Snowflake users.

### Shared OS user limits

If every agent runs as the **same OS user**, one agent can read another’s key
file under `state/agents/`. Mitigations:

- Per-agent OS users
- A separate broker service (not implemented — see below)
- Filesystem ACLs outside Snowbeam

## Rate-limit tampering

The lockout file is owner-only and HMAC-signed, but a **same-user attacker** can
delete or edit local state. Mitigations: separate OS users, monitoring, or a
remote attestation broker.

## Open design: launchd broker (not built)

Running Snowbeam as a **separate macOS user** or privileged broker via `launchd`
would let admin unlock happen outside agent reach and narrow Keychain ACLs to the
broker only. That would address same-user `security` and flat config access at
the cost of deployment complexity and IPC. This release documents the threat
model only; no broker ships yet.

## Commands summary

| Command | Purpose |
|---------|---------|
| `connections classify` / `unclassify` | Admin vs agent metadata |
| `connections list` | Role column; agent-filtered with proof env |
| `agents register` / `revoke` / `proof` | Agent identity lifecycle |
| `snow` | Filtered `SNOWFLAKE_HOME`, admin unlock, child-only secrets |
| `secrets set-passphrase` | Admin connections only |

See also [MacBook credential hygiene](macbook-snow-credentials.md) and the
[identity guide](security.md).
