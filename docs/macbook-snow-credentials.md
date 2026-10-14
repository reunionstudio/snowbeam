# MacBook Snowflake credential hygiene

Snowbeam helps when you run AI agents on the same macOS user account you use for
consulting work. The Snowflake CLI (`snow`) reads `PRIVATE_KEY_PASSPHRASE` from
the environment. Exporting that variable in `~/.zshrc` lets every child process
(including agents) authenticate with your key-pair connections without an extra
prompt.

Snowbeam does **not** lock your `connections.toml`. You should keep running
`snow connection test -c NAME` and other normal `snow` commands without a
Snowbeam login in front of them.

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

## 2. Store passphrases in Keychain

Naming convention: **service** `snowbeam.<connection>`, **account** `<connection>`
(where `<connection>` is the name in `connections.toml`).

```sh
snowbeam secrets set-passphrase work
```

This prompts with no echo and writes to Keychain. Snowbeam does not use
1Password/Bitwarden for this (separate from optional vault integration for
managed agents).

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

## 3. Run snow with a child-only passphrase

Snowbeam spawns `snow` with `PRIVATE_KEY_PASSPHRASE` set only in the child
process and strips `PRIVATE_KEY_PASSPHRASE`, `SNOWFLAKE_PASSWORD`, and related
variables from the environment it passes along:

```sh
snowbeam snow connection test -c work
snowbeam snow sql -q "SELECT CURRENT_USER()"
```

If no Keychain entry exists for that connection, `snowbeam snow` behaves like
plain `snow` (no passphrase injected).

## 4. Optional shell alias

Install a wrapper that delegates to `snowbeam snow`:

```sh
snowbeam local keychain-wrapper >> ~/.zshrc
# restart the terminal, or: source ~/.zshrc
```

Then use `snow` as usual; it forwards to `snowbeam snow`.

## 5. Remove profile exports

Edit `~/.zshrc`, `~/.zprofile`, and any other startup files. Remove lines such as:

```sh
export PRIVATE_KEY_PASSPHRASE=...
```

Restart Terminal, IDEs, and agent processes so they do not inherit the old
export. Run `snowbeam local scan` again until environment and shell checks are
clean.

## 6. Verify

```sh
snowbeam local scan
snowbeam snow connection test -c work
```

You can still run `/usr/local/bin/snow` directly when you do not need Keychain
injection; prefer `snowbeam snow` for key-pair connections that use stored
passphrases.

## Limits (honest)

An agent running as the **same macOS user** can still invoke `snowbeam snow` or
`security find-generic-password` if Keychain access has already been granted.
macOS may prompt when a **new** application reads an item. Stronger isolation
means a separate OS user account, a sandbox, or a dedicated machine for agents —
optional and outside Snowbeam’s scope.
