# Contributing to Snowbeam

Snowbeam is a local terminal tool for Snowflake connections and individual agent
access. Keep changes bounded and preserve its authentication and storage boundaries.

## Fast local iteration

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
make setup
make dev
```

Run these commands from the Snowbeam checkout. `make dev` runs the current source
in the project's editable uv environment. Edit the source, return to the demo,
press Escape to close any form, then `q` to quit. Press Enter at the terminal
prompt to restart with your edits; type `q` there to finish the loop. Python and
embedded CSS changes take effect on restart. There is no automatic reload.

Each launch starts with fresh synthetic connections, identities, aliases, and
notes. Demo edits disappear on exit. It does not read your Snowflake configuration
or contact Snowflake or vault providers. The Homebrew-installed `snowbeam` remains
the published version; use `make dev` or `make demo` to see your local edits.
No version bump, build, reinstall, or release is needed for this loop.

`make demo` runs once. In the demo, press `a` for the blank Add form and its
**Clone from** invitation, or Shift+`c` to clone the selected connection. These
cloning controls are in the working source for the next release.

During an adjustment, run the relevant tests:

```sh
make test TESTS="tests/test_tui.py -k clone"
make test TESTS="tests/test_config.py tests/test_inventory.py"
```

When a batch is ready, run `make check` for lint, formatting, and the full suite.
Use `make screenshots` after a visible UI batch. Documentation captures use
synthetic data; preserve the approved pixel logo and its original source artwork.
Commit coherent batches while iterating. Publish a version when the owner asks
to release the batch, following the [release checklist](docs/releasing.md).

Without Make, use `uv sync --frozen`, `uv run --frozen snowbeam --demo`,
`uv run --frozen pytest -q`, and the Ruff commands in the release checklist.

## Changes and bug reports

Explain the behavior being changed and how you checked it. Include Snowbeam,
Python, OS, and relevant provider CLI versions in bug reports. Use synthetic
identifiers and sanitized reproduction steps. See [SECURITY.md](SECURITY.md) for
private reporting of credential exposure or access-control issues.

Tests must isolate configuration and metadata paths. Never run the suite against
real accounts, vaults, credentials, or a contributor's Snowflake configuration.
New behavior should have focused tests where a regression could affect data,
authority, credentials, or recovery. Keep failures and incomplete verification
visible; do not convert missing evidence into successful checks.

Ordinary refreshes must remain metadata-only. Managed mutations require the
existing account-bound plan/apply review and journal. Do not weaken Snowflake
policies, share an operator's credentials, or treat runtime reports as signed
Snowflake attestations. Read the [security guide](docs/security.md) before changing
identity, provisioning, vault, or runtime behavior.

## Versions

`pyproject.toml` is the version source of truth. The installed package supplies
`snowbeam --version`; do not add another hard-coded version in application code.
Update the version, lockfile, changelog, and release notes together. Alpha versions
use Python's format, such as `0.2.0a1`, with a matching Git tag `v0.2.0a1`.

See [releasing](docs/releasing.md) for maintainer steps and validation limits.
