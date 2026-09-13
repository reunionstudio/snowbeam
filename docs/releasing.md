# Releasing Snowbeam

Snowbeam ships versioned GitHub release archives. The Homebrew tap installs from
the source archive; Linux users can install the wheel directly with uv. PyPI and
AUR are not required for these routes.

## Prepare a release

Update `pyproject.toml` with `uv version`, refresh `uv.lock`, and update the
changelog, release notes, and versioned installation examples. Use Python version
syntax, such as `0.2.0a1`, with matching tag `v0.2.0a1`. Keep alpha status and
unvalidated integrations explicit. Never replace a published version or asset.

Work from a clean checkout of the exact release commit. Use an empty `dist`
directory so stale archives cannot enter a release. Run:

```sh
uv sync --frozen
uv run ruff check src tests tools
uv run ruff format --check src tests tools
uv run pytest -q
uv build
uv run twine check --strict dist/*.whl dist/*.tar.gz
uv run python tools/check_release.py dist --tag v0.2.0a1 --prerelease true
uv run python tools/homebrew_formula.py dist
ruby -c dist/homebrew/snowbeam.rb
```

The package check installs the wheel and source archive independently outside the
checkout, checks the version and icons, and runs the synthetic inventory and
terminal UI. Only successful checks write `dist/SHA256SUMS`. The formula is
generated from that exact source archive and locked runtime dependencies.

## Publish the checked artifacts

Alpha releases currently use local publication. GitHub Actions are supplied as
[setup templates](github-actions/README.md) while workflow authorization is
pending. Hosted Linux/macOS CI is not yet enabled or validated.

1. Review repository contents and release notes. Run the checks above against
   the selected commit. Keep configuration, credentials, caches, and generated
   runtime bundles out of the repository and archives.
2. Push the reviewed commit and create a draft GitHub release at that exact
   commit, with matching tag and prerelease flag. Attach the verified wheel,
   source archive, `SHA256SUMS`, and `dist/homebrew/snowbeam.rb`.
3. After owner approval, publish the release. Download its artifacts anonymously
   and verify the checksums. Test the public wheel URL in an isolated uv tool
   installation before advertising it.
4. Copy the formula from that release to `Formula/snowbeam.rb` in
   `reunionstudio/homebrew-tap`. Check the public source URL and checksum, run a
   full Homebrew installation and `brew test`, then publish the tap and verify
   installation through `brew install reunionstudio/tap/snowbeam`.
5. Update the website with those verified destinations and the release's
   validation limits. Record what was tested and what remains pending.

Use the same checked artifacts throughout. To test a formula before release,
`uv run python tools/homebrew_formula.py dist --local` emits a separate formula
with a local archive URL. Use a disposable tap; never publish that variant.

After GitHub Actions are enabled, require a successful Checks run on the release
commit and use the Publish workflow's verified artifacts instead of local builds.
That workflow attaches its own archives, checksums, and formula. Do not manually
upload competing builds to the same release.

## Updates

Each new Snowbeam version needs a new release and a regenerated Homebrew formula.
Homebrew dependency rebuilds may also require a formula revision. Release checks
announce GitHub releases; the tap must be updated before Homebrew can install one.

The **Update and restart** button only upgrades an installation from
`reunionstudio/tap`. It checks package identity and version, delegates installation
to Homebrew, and verifies the installed version before reopening the app. The
official-tap button upgrade from 0.2.0a1 to 0.2.0a2 passed on macOS ARM64 with an
isolated inventory. Linux uv installations currently show release notices and
manual reinstallation commands.

The package manager preserves configuration and the local SQLite cache outside
the installed package. Test upgrades with synthetic retained configuration,
inventory, and operation history. See `tools/check_upgrade.py` and the
[validation record](releases/0.2.0a2-validation.md).

## Optional distribution channels

PyPI can be enabled later using Trusted Publishing, with owner `reunionstudio`,
repository `snowbeam`, workflow `publish.yml`, and protected environment `pypi`.
Verify the package name is available, configure required reviewers, then set
`PYPI_PUBLISH_ENABLED=true`. A missing public package does not reserve its name.
No long-lived PyPI token is needed. Test a published package in an isolated
environment before advertising PyPI installation.

An AUR recipe and Omarchy updater integration remain planned. The current
[Omarchy and Linux guide](omarchy.md) uses uv. A macOS DMG is outside this terminal
alpha; a future Mac app would require its own packaging and update design.

## Before a stable release

Use a dedicated Snowflake test account and vault, with explicit approval for each
account-bound plan. Record sanitized results and tool versions:

| Area | Required evidence | Current state |
| --- | --- | --- |
| Snowflake | Provision a least-privilege agent, verify access, rotate, verify the replacement from its runtime, retire the old credential, and revoke | Pending |
| Vaults | Real 1Password and Bitwarden write/read, failure recovery, and cleanup | Pending |
| Workload identity | Authenticate each supported provider/runtime combination | Pending |
| Omarchy | Install, launch, update, and remove the terminal launcher on a real desktop | Pending |
| Desktop reminders | Deliver a notification through macOS launchd and Linux systemd, then remove the job | Pending |
| Distribution | Hosted Linux/macOS CI and Intel Mac installation | Pending; official-tap button upgrade passed on macOS ARM64 |

Automated test doubles and app captures do not replace live evidence. A stable
release needs a support matrix based on completed validation.
