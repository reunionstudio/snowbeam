"""Opt-in desktop launchers and reminders; no remote service or telemetry."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from platformdirs import user_data_path

from .config import atomic_write
from .installation import homebrew_command
from .service import Service
from .snowflake import safe_text
from .store import Store, utcnow


def send_notification(title: str, body: str) -> bool:
    title, body = safe_text(title), safe_text(body)
    if sys.platform == "darwin":
        # Data is passed as argv, never interpolated into AppleScript source.
        command = [
            "osascript",
            "-e",
            "on run argv\n"
            "display notification (item 2 of argv) with title (item 1 of argv)\n"
            "end run",
            title,
            body,
        ]
    elif shutil.which("notify-send"):
        command = ["notify-send", "--app-name=Snowbeam", "--", title, body]
    else:
        return False
    try:
        return subprocess.run(command, capture_output=True, timeout=15, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def notify_due(store: Store, alerts: list[dict], coverage: list[str]) -> tuple[int, int]:
    sent = failed = 0
    for alert in alerts:
        key = alert["notice_key"]
        if store.notice_sent(key):
            continue
        detail = (
            alert["expiry_label"] if alert["status"] in {"ACTIVE", "EXPIRED"} else alert["status"]
        )
        body = (
            f"{alert['organization']} / {alert['account_name']} / {alert['user_name']}: "
            f"{alert['name']} — {detail}."
        )
        if alert["connections"]:
            body += " Connections: " + ", ".join(alert["connections"])
        if alert["stale"]:
            body += " Based on cached metadata; refresh to verify."
        if send_notification("Snowbeam · token needs attention", body):
            store.mark_notice(key)
            sent += 1
        else:
            failed += 1
    if coverage:
        digest = hashlib.sha256("\n".join(sorted(coverage)).encode()).hexdigest()
        key = f"coverage:{utcnow().date()}:{digest}"
        if not store.notice_sent(key):
            if send_notification(
                "Snowbeam · inventory needs attention",
                "Some connections or token inventories are unverified. Open Snowbeam to review.",
            ):
                store.mark_notice(key)
                sent += 1
            else:
                failed += 1
    return sent, failed


def program_args(service: Service) -> list[str]:
    # Homebrew's opt path survives version changes and cleanup of old kegs.
    command = homebrew_command()
    result = ([str(command)] if command else [sys.executable, "-m", "snowbeam"]) + [
        "--state-dir",
        str(service.store.directory.absolute()),
        "--snow-config",
        str(service.config.path),
    ]
    if service.client.executable:
        result.extend(["--snow-executable", str(Path(service.client.executable).absolute())])
    return result


def quoted_exec(args: list[str], *, systemd: bool = False) -> str:
    """Quote freedesktop Exec/systemd arguments, not shell commands."""
    result = []
    for arg in args:
        if any(ord(char) < 32 for char in arg):
            raise ValueError("Launcher paths cannot contain control characters.")
        value = arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
        if systemd:
            value = value.replace("$", "$$")
        else:
            value = value.replace("`", "\\`").replace("$", "\\$")
        result.append('"' + value + '"')
    return " ".join(result)


def install_launcher(service: Service) -> Path:
    if sys.platform != "linux":
        raise ValueError(
            "The desktop launcher is for Linux. On macOS, run snowbeam in your terminal."
        )
    directory = user_data_path(appauthor=False) / "applications"
    path = directory / "snowbeam.desktop"
    icon = directory.parent / "icons/hicolor/512x512/apps/snowbeam.png"
    atomic_write(icon, files("snowbeam").joinpath("assets/snowbeam-512.png").read_bytes())
    icon_value = str(icon).replace("\\", "\\\\")
    atomic_write(
        path,
        "[Desktop Entry]\nType=Application\nName=Snowbeam\n"
        "Comment=Snowflake connections and individual agent access\n"
        f"Exec={quoted_exec(program_args(service))}\nTerminal=true\n"
        f"Icon={icon_value}\nCategories=Development;Utility;\n"
        "Keywords=Snowflake;PAT;Connections;Agents;\n",
    )
    return path


def install_reminders(service: Service) -> list[Path]:
    args = program_args(service) + ["check", "--refresh", "--notify"]
    if sys.platform == "linux":
        directory = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
        )
        service_path = directory / "snowbeam-reminders.service"
        timer_path = directory / "snowbeam-reminders.timer"
        atomic_write(
            service_path,
            "[Unit]\nDescription=Check Snowbeam token expirations\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={quoted_exec(args, systemd=True)}\n"
            "SuccessExitStatus=1\nTimeoutStartSec=10min\n",
        )
        atomic_write(
            timer_path,
            "[Unit]\nDescription=Daily Snowbeam expiration reminders\n\n"
            "[Timer]\nOnCalendar=*-*-* 09:00:00\nPersistent=true\n"
            "RandomizedDelaySec=60\n\n[Install]\nWantedBy=timers.target\n",
        )
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", "snowbeam-reminders.timer"])
        return [service_path, timer_path]
    if sys.platform == "darwin":
        path = Path.home() / "Library/LaunchAgents/io.reunionstudio.snowbeam.plist"
        payload = {
            "Label": "io.reunionstudio.snowbeam",
            "ProgramArguments": args,
            "StartInterval": 3600,
            "RunAtLoad": True,
            "ProcessType": "Background",
        }
        atomic_write(path, plistlib.dumps(payload).decode())
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain + "/io.reunionstudio.snowbeam"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        _run(["launchctl", "bootstrap", domain, str(path)])
        return [path]
    raise ValueError("Desktop reminders currently support Linux with systemd and macOS.")


def remove_reminders() -> None:
    if sys.platform == "linux":
        _run(["systemctl", "--user", "disable", "--now", "snowbeam-reminders.timer"])
        directory = (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
        )
        for name in ("snowbeam-reminders.service", "snowbeam-reminders.timer"):
            (directory / name).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
    elif sys.platform == "darwin":
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}/io.reunionstudio.snowbeam"],
            capture_output=True,
            check=False,
            timeout=15,
        )
        (Path.home() / "Library/LaunchAgents/io.reunionstudio.snowbeam.plist").unlink(
            missing_ok=True
        )
    else:
        raise ValueError("Desktop reminders currently support Linux with systemd and macOS.")


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(
            "Could not reach the desktop scheduler. No running reminder was confirmed."
        ) from None
    if result.returncode:
        raise ValueError(
            "Scheduler rejected activation. Files were written; "
            "check the desktop session and retry."
        )
