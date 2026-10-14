"""Interactive TTY helpers (not answerable from piped stdin)."""

from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Callable


def stdin_is_interactive_tty() -> bool:
    try:
        return sys.stdin.isatty() and os.isatty(sys.stdin.fileno())
    except (AttributeError, OSError, ValueError):
        return False


def open_controlling_tty() -> int | None:
    try:
        return os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return None


def tty_getpass(
    prompt: str,
    *,
    opener: Callable[[str, int], int] | None = None,
) -> str | None:
    if not stdin_is_interactive_tty():
        return None
    open_fn = opener or (lambda path, flags: os.open(path, flags))
    fd = open_fn("/dev/tty", os.O_RDWR)
    if fd < 0:
        return None
    try:
        with os.fdopen(fd, "r+", encoding="utf-8") as tty:
            return getpass.getpass(prompt, stream=tty)
    except (OSError, ValueError):
        return None
