# Changelog

Versions follow Python package versioning. Pre-release tags match package versions
with a `v` prefix. This file records user-visible changes and known limitations.

## 0.2.0a6 — Unreleased

- Add read-only `snowbeam local scan` for Snowflake CLI credential hygiene on
  this machine (configs, keys, permissions, shell exports, environment).
- Add `snowbeam snow` to run the Snowflake CLI with `PRIVATE_KEY_PASSPHRASE`
  only in the child process when a passphrase is stored in macOS Keychain (or
  Linux `secret-tool` when available).
- Add `snowbeam secrets set-passphrase`, `status`, and `remove` for OS-backed
  connection passphrases; optional `snowbeam local keychain-wrapper` shell snippet.
- Document MacBook setup in [docs/macbook-snow-credentials.md](docs/macbook-snow-credentials.md).

## 0.2.0a5 — Unreleased

- Default managed-agent credential handoff is **human relay**: after an approved
  apply, Snowbeam prints suggested password-manager fields once, requires
  `identities confirm-relay`, and verifies with `--token-file` or
  `--private-key-file` (secrets are not persisted in SQLite or logs).
- Password-manager CLI integration is **opt-in** via
  `preferences.vault_integration` (off by default). Templates omit vault fields
  unless enabled.
- Document the implemented managed-agent lifecycle: reviewed plan/apply, runtime
  export and verification, rotation, retirement, and revocation; optional vault
  writes when integration is enabled.
- Add v1 access-request YAML import, capability checks, role starters, account
  admin mapping, lifetime tracking, and connection hygiene helpers.
- Add an inspect-only agent SQL example (for example read-only `SHOW` metadata)
  to the identity and security guide.
- Block lifecycle actions in demo mode with an explicit message.
- Add CLI lifecycle tests and a validation note for mocked Snowflake/vault flows.

## 0.2.0a4 — Unreleased

- Offer **Clone from** at the top of the blank Add connection form. Selecting a
  source fills its editable settings and suggests an available name.
- Add **Clone** (Shift+`c`) for the selected connection, opening the same workflow
  with the source already selected. Keep the source selector and Save/Cancel
  controls visible while the fields scroll.
- Support `connections add NAME --clone-from SOURCE` and
  `connections clone SOURCE NAME` with field overrides on the CLI.
- Copy saved connection settings without duplicating credential files, secrets,
  vault bindings, environment overrides, default status, or verification evidence.

## 0.2.0a3 — 2026-09-13

- Use **Organizations** for the tree heading and **Accounts** for the CLI inventory
  heading. Update the empty-state guidance and synthetic app captures to match.

## 0.2.0a2 — 2026-09-13

- Give organizations and accounts their own local aliases and multiline notes
  through the tree's **Alias / notes** editor (`n`) or `snowbeam labels`.
- Show friendly names in the inventory, retain real identifiers in details and
  clipboard copies, and search identities by organization/account alias.
- Preserve annotations across refreshes, restarts, and upgrades. Account labels
  follow the existing stable account ID when a rename is recognized.
- Migrate local inventory to schema 4 and include annotations as separate fields
  in `inventory --json`. Earlier app versions cannot open the migrated database.

See the [release notes](docs/releases/0.2.0a2.md) for installation and validation limits.

## 0.2.0a1 — 2026-09-13

First alpha release of Snowbeam.

- Browse connections, identities, token metadata, and attention across Snowflake
  organizations and accounts in a local terminal app.
- Track client, owner, and runtime labels; inspect policy and role-grant evidence.
- Plan individual agent provisioning, PAT/key rotation and revocation, and existing
  workload-identity bindings with account guards, vault references, and journals.
- Use a credential-free synthetic demo, CLI/JSON commands, and optional Linux
  launchers or Linux/macOS reminders.
- Include the approved pixel logo in the installed package.
- Add alpha package metadata, a single version source, release checks, and concise
  installation, upgrade, uninstall, contribution, and security documentation.
- Generate a Homebrew formula from verified release artifacts and locked runtime
  dependencies; use stable Homebrew paths for newly installed reminders.
- Show update availability and upgrade instructions, with explicit checks or
  optional daily public release checks. Offline/demo mode never checks online.
- Add **Update and restart** for official Homebrew installations: confirm the
  update, show progress, verify the installed version, and reopen the same local
  configuration. Failed upgrades stay in the app and can be retried.
- Configure background refresh per connection, retain schedules across restarts,
  and back off after failures while preserving the last successful metadata.

The earlier `0.2.0` value was used in private development builds; it was not a
public release. That development build is not a newer stable release of this alpha.

Managed agent lifecycle is implemented and covered by isolated automated tests.
Live Snowflake and vault trials on each supported platform, cloud workload
authentication on real cloud runtimes, and Omarchy desktop behavior still need
broader validation. See [0.2.0a5 validation](docs/releases/0.2.0a5-validation.md).
