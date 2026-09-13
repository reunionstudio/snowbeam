"""User-requested Homebrew upgrades with fixed commands and sanitized progress."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from packaging.version import Version

from . import __version__
from .installation import homebrew_command

FORMULA = "reunionstudio/tap/snowbeam"


class UpgradeError(Exception):
    """A safe explanation that never includes raw installer output."""


@dataclass(frozen=True)
class Restart:
    command: Path


@dataclass(frozen=True)
class HomebrewUpgrade:
    brew: Path
    command: Path

    def install(self, requested: str, progress: Callable[[str], None]) -> Restart:
        current, requested_version = Version(__version__), Version(requested)
        if requested_version <= current:
            raise UpgradeError("No newer Snowbeam release is selected.")
        environment = installer_environment()
        progress("Updating Homebrew’s package catalog…")
        run_installer([str(self.brew), "update"], environment, timeout=180)
        progress("Checking the Snowbeam package…")
        metadata = run_installer(
            [str(self.brew), "info", "--json=v2", "--formula", FORMULA],
            environment,
            timeout=60,
            capture=True,
        )
        candidate = package_version(metadata)
        if candidate < requested_version or candidate <= current:
            raise UpgradeError(
                "This release is not available in the Homebrew tap yet. Try again later."
            )
        if candidate.is_prerelease and not current.is_prerelease:
            raise UpgradeError(
                "The Homebrew tap currently offers a prerelease. Stable updates only."
            )
        progress(f"Installing Snowbeam {candidate}…")
        run_installer([str(self.brew), "upgrade", "--formula", FORMULA], environment, timeout=1800)
        progress("Verifying the updated installation…")
        output = run_installer(
            [str(self.command), "--version"], environment, timeout=30, capture=True
        )
        try:
            text = output.decode("utf-8").strip()
            if not text.startswith("Snowbeam "):
                raise ValueError("Missing version")
            installed = Version(text.removeprefix("Snowbeam "))
            if installed < candidate:
                raise ValueError("Older version")
        except (ValueError, UnicodeError):
            raise UpgradeError(
                "Homebrew finished, but the updated Snowbeam could not be verified. "
                "Check the installation before restarting."
            ) from None
        progress("Update installed. Restarting Snowbeam…")
        return Restart(self.command)


def homebrew_upgrader() -> HomebrewUpgrade | None:
    command = homebrew_command()
    if command is None:
        return None
    # Only the official tap is eligible. Never switch another distributor's app.
    receipt = Path(sys.prefix).resolve().parent / "INSTALL_RECEIPT.json"
    try:
        source = json.loads(receipt.read_text())["source"]
        if source.get("tap") != "reunionstudio/tap":
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    brew = command.parents[3] / "bin/brew"
    if not all(path.is_file() and os.access(path, os.X_OK) for path in (command, brew)):
        return None
    return HomebrewUpgrade(brew, command)


def installer_environment() -> dict[str, str]:
    # Preserve normal OS/locale/proxy settings, but never forward vault, Snowflake,
    # cloud, or GitHub tokens from the application's environment to the installer.
    allowed = {
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TMPDIR",
        "LANG",
        "TERM",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    result = {k: v for k, v in os.environ.items() if k in allowed or k.startswith("LC_")}
    result.update(
        HOMEBREW_NO_ANALYTICS="1",
        HOMEBREW_NO_AUTO_UPDATE="1",
        HOMEBREW_NO_ASK="1",
        # The old UI/interpreter must remain intact until it exits after success.
        HOMEBREW_NO_INSTALL_CLEANUP="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return result


def run_installer(
    command: list[str], environment: dict[str, str], *, timeout: int, capture: bool = False
) -> bytes:
    try:
        with subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        ) as process:
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Stop the whole installer group, including a build/download child.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                except ProcessLookupError:
                    pass
                raise UpgradeError(
                    "The update timed out. Check Homebrew before trying again."
                ) from None
            if process.returncode:
                raise UpgradeError(
                    "Homebrew could not complete the update. You can retry, or run Homebrew "
                    "in a terminal for diagnostics. Snowbeam has not restarted."
                )
    except OSError:
        raise UpgradeError(
            "The installer could not start. Check your Homebrew installation."
        ) from None
    if output and len(output) > 1024 * 1024:
        raise UpgradeError(
            "Homebrew returned an unexpected response. The update could not be verified."
        )
    return output or b""


def package_version(output: bytes) -> Version:
    try:
        formulae = json.loads(output)["formulae"]
        if len(formulae) != 1:
            raise ValueError("Unexpected package count")
        package = formulae[0]
        if package["full_name"] != FORMULA or package["tap"] != "reunionstudio/tap":
            raise ValueError("Unexpected package source")
        if package.get("pinned"):
            raise UpgradeError("Snowbeam is pinned in Homebrew. Unpin it before updating.")
        if package.get("disabled"):
            raise UpgradeError(
                "Homebrew has disabled this package. Review the release before updating."
            )
        return Version(package["versions"]["stable"])
    except (ValueError, KeyError, TypeError):
        raise UpgradeError("Homebrew’s Snowbeam release metadata could not be verified.") from None


def restart_app(command: Path, args, service) -> None:
    arguments = [
        str(command),
        "--state-dir",
        str(service.store.directory.resolve()),
        "--snow-config",
        str(service.config.path),
        "--fleet-config",
        str(service.fleet.config.path),
    ]
    if args.snow_executable:
        arguments.extend(["--snow-executable", args.snow_executable])
    arguments.append("tui")
    # Launch the stable Homebrew path directly, even if an older uv tool shadows it.
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONHOME", "PYTHONPATH"}}
    try:
        os.execve(command, arguments, environment)
    except OSError:
        raise ValueError("Snowbeam was updated but could not reopen. Run snowbeam again.") from None
