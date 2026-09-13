# Install and update on Omarchy or Linux

Snowbeam is a Python terminal application built first for Omarchy. The Linux
installation route currently uses uv with a release wheel or source checkout.
An AUR package and an in-app Omarchy updater are planned, not available. Actual
Omarchy desktop validation remains pending.

## Install the alpha

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, run:

```sh
uv tool install https://github.com/reunionstudio/snowbeam/releases/download/v0.2.0a3/snowbeam-0.2.0a3-py3-none-any.whl
snowbeam --version
snowbeam --demo
```

Snowbeam requires Python 3.11 or newer; uv can provision a compatible interpreter.
No Git checkout or local build is needed. The [release page](https://github.com/reunionstudio/snowbeam/releases/tag/v0.2.0a3)
also provides the wheel, source archive, and checksums for manual downloads.

If you do not have uv, install it using Astral's official installer, then reopen
your terminal before running the commands above:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

If uv reports that its executable directory is missing from PATH, run
`uv tool update-shell` and reopen your terminal. The demo uses temporary synthetic
data, makes no Snowflake or vault calls, and enables no background jobs.

Live inventory requires Snowflake CLI (`snow`) and configured connections. Vault
workflows require the chosen provider's CLI and authorization. See the
[user guide](user-guide.md) before using live connections.

## Add the pixel icon to the app launcher

After installation, run:

```sh
snowbeam desktop install
```

This writes a user-level desktop entry and bundled icon. It leaves Omarchy's
configuration in place. The desktop must support terminal applications.
Alternatively, use Omarchy's **Install → TUI** with name `Snowbeam`, command
`snowbeam`, your preferred window style, and the bundled icon. Choose one launcher
method. See [Omarchy's TUI instructions](https://omarchy.org/manual/tuis/#adding-your-own).

## Update a uv installation

The Updates panel can check GitHub releases and show availability. The installer
button currently supports official Homebrew installations only. Quit Snowbeam,
use the new release's wheel URL, and run:

```sh
uv tool install --reinstall https://github.com/reunionstudio/snowbeam/releases/download/vNEW_VERSION/snowbeam-NEW_VERSION-py3-none-any.whl
snowbeam --version
snowbeam desktop install
```

Replace `NEW_VERSION` with the downloaded version. Reinstall optional reminders
after an upgrade if you previously enabled them. Your configuration, preferences,
and local SQLite inventory stay outside the installed package. Refresh intervals
and failure backoff persist; no separate database server is needed.

## Planned AUR distribution

The intended Omarchy distribution uses an Arch package recipe (`PKGBUILD`) pointing
to the same versioned GitHub source release used by Homebrew. Once published and
validated, users would install through **Install → AUR** and receive upgrades
through **Update → Omarchy**. The package should include the launcher and icon.
There is no `snowbeam` AUR installation command to advertise yet.

A future Snowbeam action could open Omarchy's updater, clearly explaining that it
may update other packages. It must preserve Omarchy's update process, including
snapshots and migrations. See Omarchy's [package installation](https://omarchy.org/manual/other-packages/)
and [update documentation](https://omarchy.org/manual/updates/).

## Uninstall

If reminders were enabled, run `snowbeam reminders remove` first. Then run
`uv tool uninstall snowbeam`. Remove an optional launcher through the method used
to create it; the user guide lists the files created by `snowbeam desktop install`.
Uninstalling the package preserves your configuration and inventory.
