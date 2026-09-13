# Changelog

Versions follow Python package versioning. Pre-release tags match package versions
with a `v` prefix. This file records user-visible changes and known limitations.

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

Live Snowflake provisioning, real 1Password/Bitwarden writes, cloud workload
identity authentication, and actual Omarchy desktop behavior remain unvalidated.
See the [alpha release notes](docs/releases/0.2.0a1.md).
