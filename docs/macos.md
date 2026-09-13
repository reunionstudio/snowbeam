# Install and update on macOS

Snowbeam is a terminal application. The first Mac distribution uses Homebrew;
Python and the app's dependencies are managed for you. Snowbeam is alpha software;
see the [release notes](releases/0.2.0a1.md) for validation limits.

## Install a release wheel

Download the wheel and checksums from [GitHub releases](https://github.com/reunionstudio/snowbeam/releases).
With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
uv tool install ./snowbeam-0.2.0a1-py3-none-any.whl
snowbeam --version
snowbeam --demo
```

Live inventory also needs Snowflake CLI (`snow`) and your configured connection.
Managed vault workflows need the chosen vault CLI and authorization. The demo
needs neither. Open `snowbeam tui --offline` to inspect existing cached data.

## Install with Homebrew

The release process generates `dist/homebrew/snowbeam.rb` from the verified source
archive and locked runtime dependencies. With [Homebrew](https://brew.sh/) installed:

```sh
brew install reunionstudio/tap/snowbeam
snowbeam
```

The [Reunion Studio tap](https://github.com/reunionstudio/homebrew-tap) points to
versioned GitHub release assets. Homebrew manages an isolated Python
environment. It does not install Snowflake or vault CLIs implicitly.

When switching from an existing uv installation to Homebrew, check `which snowbeam`
after installation. A command in `~/.local/bin` can precede Homebrew in your PATH.
Remove the old uv tool with `uv tool uninstall snowbeam` once you have verified the
Homebrew installation; this preserves your configuration and inventory. Reinstall
optional reminders so they use the new installation's stable command.

## Update from inside Snowbeam

Choose **Updates** (or press `u`) to see the installed version, last verified
release, and instructions for your installation. **Check now** requests public
release metadata from GitHub. If a newer version is known, the main button reads
**Update available**. For an installation from the official Homebrew tap, the panel
offers **Update and restart**. Clicking it confirms the installation:

1. Finish any active Snowflake operation before starting the update.
2. The app shows progress while Homebrew updates its catalog and installs Snowbeam.
3. It verifies the new version, then reopens using the same configuration and cache.

There are no commands to copy. Homebrew may update required dependencies and repair
affected packages. The update runs only after that click; automatic release checks
never start an installation. Close and Quit are disabled during installation. If
the update fails, the app stays open with a safe error message and lets you retry.
Installer output and credential environment variables are not copied into the UI.

The button checks the installed Homebrew receipt and only upgrades
`reunionstudio/tap/snowbeam`. It does not switch a source/wheel installation or
another distributor's formula to the official tap. Those installations retain
manual instructions. Demo and offline mode cannot run the installer.

Automatic checks are off by default. Enable **Check automatically once a day** in
the panel, or use:

```sh
snowbeam updates enable
snowbeam updates check
snowbeam updates status
snowbeam updates disable
```

Automatic checks run at most daily while the app is open. Demo and offline mode
make no update requests. Checks send no Snowflake account information, credentials,
or installation identifier. GitHub receives an ordinary HTTPS request, including
the network's IP address. Failed checks retain the previous result and its date.
Stable installations look for stable releases; alpha installations also include
prereleases. A release announcement can precede its Homebrew formula update.

You can also upgrade a Homebrew installation manually. Quit Snowbeam and run:

```sh
brew update
brew upgrade reunionstudio/tap/snowbeam
snowbeam --version
snowbeam
```

Homebrew does not automatically upgrade Snowbeam just because the app is open.
During an in-app update, Snowbeam keeps the old installation files available until
it exits, then launches the updated Homebrew command directly. This also avoids an
older uv installation earlier in PATH. Source/wheel installations show
reinstallation instructions rather than switching installation methods.
For a new wheel:

```sh
uv tool install --reinstall ./snowbeam-NEW_VERSION-py3-none-any.whl
```

Your Snowflake configuration, companion identity configuration, preferences, and
SQLite inventory live outside the installed package and survive an upgrade or
uninstall. The metadata cache upgrades its schema when needed; older app versions
may reject a newer cache. Keep a private backup before downgrading. The database
is not encrypted; it must never contain credential values.

New Homebrew reminder installations use `/opt/homebrew/opt/snowbeam/bin/snowbeam`
on a standard Apple Silicon installation (the equivalent Homebrew prefix on other
systems). This stable path follows upgrades. After switching installation methods,
or upgrading an older reminder installation, run `snowbeam reminders install`
again. Source/wheel installations also need reminders reinstalled after upgrading.
Installing Snowbeam never enables a background job automatically.

Before uninstalling, remove reminders if enabled:

```sh
snowbeam reminders remove
brew uninstall reunionstudio/tap/snowbeam
```

## DMG and native Mac UI

A DMG is not part of this terminal alpha. A future downloadable Mac application
would need a bundled runtime, a terminal launcher or a native interface, Developer
ID signing, notarization, architecture testing, and its own update mechanism.
Homebrew is the Mac distribution for this alpha.
