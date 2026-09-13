# Snowbeam

<img src="https://reunionstudio.io/assets/snowbeam/logo-256.png" alt="Snowbeam pixel logo: a person in a transporter beam dissolving into snow" width="96" height="96">

**Your way into Snowflake.**

Manage many Snowflake connections for many agents, across organizations and
accounts. Snowbeam is a local terminal app built first for
[Omarchy](https://omarchy.org/manual/), with Linux and macOS support.

Give each autonomous agent its own identity and only the access its job needs.
Keep client accounts separate, inspect policy evidence, and manage individual
credentials through reviewed plans. Snowflake enforces access; 1Password or
Bitwarden holds managed tokens and private keys; your chosen runtime runs the
agent. Snowbeam works independently of Airlock.

**Alpha: `0.2.0a1`.** Source and versioned downloads are available through
[GitHub releases](https://github.com/reunionstudio/snowbeam/releases). Live Snowflake provisioning, vault writes, cloud
workload authentication, and Omarchy desktop validation remain pending. See the
[alpha release notes](https://github.com/reunionstudio/snowbeam/blob/main/docs/releases/0.2.0a1.md).

![Snowbeam identity inventory with synthetic data](https://reunionstudio.io/assets/snowbeam/identities.png)

## Install and try the demo

On macOS, with [Homebrew](https://brew.sh/) installed:

```sh
brew install reunionstudio/tap/snowbeam
snowbeam --version
snowbeam --demo
```

Homebrew manages Python and the app's dependencies. See the
[macOS guide](docs/macos.md) for installation and **Update and restart**.

On Omarchy or another Linux desktop, with
[uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
uv tool install https://github.com/reunionstudio/snowbeam/releases/download/v0.2.0a1/snowbeam-0.2.0a1-py3-none-any.whl
snowbeam --version
snowbeam --demo
```

No checkout or build step is needed. uv manages an isolated environment and can
provision Python 3.11 or newer. After quitting the demo, `snowbeam desktop install`
adds the pixel icon and terminal launcher on Linux. See the
[Omarchy and Linux guide](docs/omarchy.md), including first-time uv setup.

The demo uses temporary, synthetic data. It does not read your Snowflake
configuration, connect to Snowflake, or enable desktop reminders. Press `q` to quit.
If uv reports that its executable directory is missing from `PATH`, run
`uv tool update-shell` and reopen your terminal.

The Homebrew formula uses checksummed sources and locked runtime dependencies.
No Snowbeam package is published to PyPI or AUR, and no macOS DMG is provided.
Downloads and `SHA256SUMS` are on the
[alpha release](https://github.com/reunionstudio/snowbeam/releases/tag/v0.2.0a1).

## Connect to your inventory

Live checks require [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/index)
as the `snow` command. Install it separately and configure your connections using
Snowflake CLI's authentication guidance. Vault-backed workflows also require the
chosen provider's CLI and authorization.

```sh
uv tool install snowflake-cli
snowbeam tui --offline
```

Offline mode lets you inspect cached inventory without automatic refreshes.
Running `snowbeam` normally refreshes eligible connections when due, hourly by
default. Per-connection intervals and failure backoff persist in local state.
Browser sign-in requires an explicit refresh. Creating,
rotating, or revoking a managed credential requires a reviewed plan and apply.

## Upgrade or uninstall

Open **Updates** (`u`) for available versions and installation-specific upgrade
instructions. Checks are manual by default; optionally enable daily checks while
the app is open. An official Homebrew installation offers **Update and restart**:
click it to install through Homebrew, see progress, and reopen the updated app.
Active Snowflake work must finish first. If installation fails, Snowbeam stays open
and lets you retry. Release checks never install updates without that click.
Manual Homebrew upgrades remain available through `brew update` followed by
`brew upgrade reunionstudio/tap/snowbeam` with Snowbeam closed.

For a Linux uv installation, quit Snowbeam and reinstall using the new release's
wheel URL:

```sh
uv tool install --reinstall https://github.com/reunionstudio/snowbeam/releases/download/vNEW_VERSION/snowbeam-NEW_VERSION-py3-none-any.whl
snowbeam --version
```

Replace `NEW_VERSION` with the chosen release. The Linux Updates panel announces
releases; the in-app installer currently supports Homebrew only. Reinstall optional
source/wheel launchers or reminders after an
upgrade so they refer to the current environment. New Homebrew reminders use a
stable path that follows package upgrades.

If you enabled reminders, remove them before uninstalling:

```sh
snowbeam reminders remove
```

Then uninstall using the method you installed with:

```sh
brew uninstall reunionstudio/tap/snowbeam
```

Or, for a uv installation:

```sh
uv tool uninstall snowbeam
```

Uninstalling the package preserves your Snowflake configuration and local
inventory. Linux users who installed the optional launcher can remove
`snowbeam.desktop` from their user applications directory and `snowbeam.png`
from their user `icons/hicolor/512x512/apps` directory. The
[user guide](https://github.com/reunionstudio/snowbeam/blob/main/docs/user-guide.md)
describes configuration, storage paths, launchers, and reminders.

## Read more

- [User guide](https://github.com/reunionstudio/snowbeam/blob/main/docs/user-guide.md): terminal controls, inventory, configuration, CLI commands, and desktop integration.
- [Identity and security guide](https://github.com/reunionstudio/snowbeam/blob/main/docs/security.md): vaults, plan/apply, runtime handoff, rotation, and recovery.
- [Changelog](https://github.com/reunionstudio/snowbeam/blob/main/CHANGELOG.md) and [release process](https://github.com/reunionstudio/snowbeam/blob/main/docs/releasing.md).
- [Contributing](https://github.com/reunionstudio/snowbeam/blob/main/CONTRIBUTING.md) and [reporting a security issue](https://github.com/reunionstudio/snowbeam/blob/main/SECURITY.md).

The alpha has local tests and package-installation checks. GitHub Actions are
[setup templates](docs/github-actions/README.md) until workflow authorization is
available; hosted Linux CI and live Omarchy testing remain pending.

MIT licensed. Made by [Reunion Studio](https://reunionstudio.io/).
