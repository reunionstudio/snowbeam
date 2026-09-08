# Snowbeam

Keep this a small, local Snowflake connection and token inventory tool.

- Snowflake CLI owns authentication. Never add token values, passwords, private
  keys, or raw CLI error output to the SQLite cache, UI, logs, or JSON exports.
- Preserve unknown TOML fields and comments. Connection edits affect real local
  configuration; keep private backups, atomic writes, and conflict detection.
- Live refreshes use fixed metadata queries only. Do not silently create,
  rotate, revoke, or renew Snowflake credentials or alter Snowflake objects.
- A successful login, a complete PAT inventory, and a PAT's expiration are
  different facts. Preserve failed/stale/unknown states and cached evidence.
- Token inventory is scoped to the current users of configured connections.
  Organization discovery is optional and privilege dependent.
- Offline use and synthetic demo mode must not require credentials. Reminders
  and desktop integration remain explicit, opt-in commands.
- Keep Linux and macOS supported with Python 3.11 or newer. Test paths must be
  isolated; never run tests against a contributor's real Snowflake config.

Before finalizing code changes, run `uv run ruff check src tests tools`,
`uv run ruff format --check src tests tools`, and `uv run pytest -q`.
Run `uv build` after packaging changes. Regenerate demo screenshots with
`uv run python tools/capture_demo.py` after visible UI changes.
