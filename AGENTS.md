# Snowbeam

Keep this a local Snowflake connection, consultant identity, and agent access tool.

- Snowflake CLI executes authentication; trusted vault adapters resolve credentials.
  Never add token values, passwords, private
  keys, or raw CLI error output to the SQLite cache, UI, logs, or JSON exports.
- Preserve unknown TOML fields and comments. Connection edits affect real local
  configuration; keep private backups, atomic writes, and conflict detection.
- Live refreshes use fixed metadata queries only. Do not silently create,
  rotate, revoke, or renew Snowflake credentials or alter Snowflake objects.
  Explicit managed-agent mutations require a fresh account-bound plan/apply
  review, same-session server guard, and an operation journal. Keep old credentials
  until the replacement passes runtime verification and retirement is approved.
- Runtime bundles contain one identity, never the manager's configuration.
  Operator-supplied runtime reports are not signed Snowflake attestations.
- Keep policy uncertainty and direct-grant inspection limits explicit. Never
  weaken a policy to make provisioning succeed. Cloud IAM stays external.
- A successful login, a complete PAT inventory, and a PAT's expiration are
  different facts. Preserve failed/stale/unknown states and cached evidence.
- Token inventory is scoped to the current users of configured connections.
  Managed-agent inspection uses its authorized template connection. Organization
  discovery is optional and privilege dependent.
- Offline use and synthetic demo mode must not require credentials. Reminders
  and desktop integration remain explicit, opt-in commands.
- Keep Linux and macOS supported with Python 3.11 or newer. Test paths must be
  isolated; never run tests against a contributor's real Snowflake config.

For UI iteration, run the working source with `make dev` or `make demo` and
synthetic data. Use focused tests while adjusting, then the full checks below
when the batch is ready. Do not bump versions, build distribution archives,
publish releases, update the tap/website, or upgrade the installed app for each
adjustment. Release the accumulated batch when the user asks for a release.

Before finalizing code changes, run `uv run ruff check src tests tools`,
`uv run ruff format --check src tests tools`, and `uv run pytest -q`.
Run `uv build` after packaging changes. Regenerate demo screenshots with
`uv run python tools/capture_demo.py` after visible UI changes.
