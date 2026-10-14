# MacBook Snowflake credential hygiene

Snowbeam helps when you run AI agents on the same macOS user account you use for
consulting work. Snowbeam is an **inventory and control plane**, not a vault
that hands admin passphrases to every child process. See
[control plane](control-plane.md) for admin vs agent connections and proofs.

The Snowflake CLI (`snow`) reads `PRIVATE_KEY_PASSPHRASE` from the environment.
Exporting that variable in `~/.zshrc` lets every child process (including
agents) authenticate with your key-pair connections without an extra prompt.

Snowbeam does **not** lock your master `connections.toml`. For agents, use
**agent-classified** connections and `snowbeam snow` with `SNOWBEAM_AGENT_*`
proofs so admin profiles never appear in the child config view.

## 1. Scan this machine

Read-only; never calls Snowflake and never prints secret values:

```sh
snowbeam local scan
snowbeam local scan --json
```

Exit codes: `0` clean, `1` medium findings only, `2` high-severity findings.

The scan checks Snowflake CLI config (`SNOWFLAKE_HOME`, default macOS paths),
`connections.toml`, `~/.snowsql/config`, plaintext secrets in files, passphrases
stored beside key paths, unencrypted private keys, shared key files, missing or
foreign key paths, loose file permissions, secret exports in your shell startup
files, and secret variables in the current environment.

## 2. Classify connections

```sh
snowbeam connections classify work --role admin
snowbeam connections classify agent-bot --role agent
```

Unlisted names behave as **admin** (agents cannot see or use them through
Snowbeam).

## 3. Store admin passphrases in Keychain

Only **admin** connections:

```sh
snowbeam secrets set-passphrase work
```

Naming convention: **service** `snowbeam.<connection>`, **account** `<connection>`.

Check status:

```sh
snowbeam secrets status
snowbeam secrets status work
```

Remove:

```sh
snowbeam secrets remove work
```

Manual alternative (Terminal prompts for the passphrase; do not pass `-w` on the
command line):

```sh
security add-generic-password -s "snowbeam.work" -a "work" -T "$(which snowbeam)" -T "$(which snow)"
```

Consider a **decoy** `export PRIVATE_KEY_PASSPHRASE=decoy-...` in the shell so
agents never see a real value; Snowbeam strips it and unlocks admin keys only on
a real TTY.

## 4. Register agents and run snow

```sh
snowbeam agents plan-register my-agent
snowbeam agents register my-agent --approve DIGEST
eval "$(snowbeam agents proof my-agent | jq -r '"export \(.SNOWBEAM_AGENT_ID)='"'"'" + .SNOWBEAM_AGENT_ID + "'"'"'; export \(.SNOWBEAM_AGENT_PROOF)='"'"'" + .SNOWBEAM_AGENT_PROOF + "'"'"'"')"
snowbeam snow connection test -c agent-bot
```

Humans run admin connections from an interactive terminal (passphrase challenge +
rate limiting). Agents get **silent** generic failure if they target admin
connections.

Snowbeam spawns `snow` with `SNOWFLAKE_HOME` pointing at a filtered view and
`PRIVATE_KEY_PASSPHRASE` set only in the child when allowed.

```sh
snowbeam snow connection test -c work
snowbeam snow sql -q "SELECT CURRENT_USER()"
```

## 5. Optional shell alias

```sh
snowbeam local keychain-wrapper >> ~/.zshrc
```

Then use `snow` as usual; it forwards to `snowbeam snow`.

## 6. Remove profile exports

Edit `~/.zshrc`, `~/.zprofile`, and any other startup files. Remove lines such as:

```sh
export PRIVATE_KEY_PASSPHRASE=...
```

Restart Terminal, IDEs, and agent processes so they do not inherit the old
export. Run `snowbeam local scan` again until environment and shell checks are
clean.

## 7. Verify

```sh
snowbeam local scan
snowbeam snow connection test -c work
```

You can still run `/usr/local/bin/snow` directly against the full config when you
accept the risk; prefer `snowbeam snow` for governed paths.

## Limits (honest)

An agent running as the **same macOS user** can still read another agent's key
file, delete lockout state, or invoke `security` if Keychain ACLs allow it.
Stronger isolation means per-agent OS users, a broker service (see
[control plane — open design](control-plane.md)), or a dedicated machine for
agents.

Touch ID / LocalAuthentication gating for Keychain is documented but not fully
automated in Snowbeam yet; use Keychain access controls manually where needed.
