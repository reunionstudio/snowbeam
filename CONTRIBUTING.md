# Contributing to Snowbeam

Snowbeam is a local terminal tool for Snowflake connections and individual agent
access. Keep changes bounded and preserve its authentication and storage boundaries.

## Development

Use Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv sync --frozen
uv run snowbeam --demo
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run pytest -q
uv build
uv run twine check --strict dist/*.whl dist/*.tar.gz
uv run python tools/check_release.py dist
```

The package check installs the wheel and source archive in separate temporary
environments, outside the checkout, then checks metadata, CLI output, demo data,
and the terminal UI. It downloads declared dependencies when necessary. It does
not read real Snowflake configuration or connect to providers.

Regenerate screenshots with `uv run python tools/capture_demo.py` after visible
UI changes. All documentation captures must use synthetic data. Preserve the
approved pixel logo and its original source artwork.

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
