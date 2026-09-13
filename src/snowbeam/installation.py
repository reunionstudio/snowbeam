"""Identify the current installation without running another package manager."""

import json
import sys
from importlib.metadata import distribution
from pathlib import Path


def homebrew_command(prefix: Path | None = None) -> Path | None:
    prefix = prefix or Path(sys.prefix)
    for parent in prefix.parents:
        if parent.name == "Cellar" and prefix.relative_to(parent).parts[0] == "snowbeam":
            return parent.parent / "opt/snowbeam/bin/snowbeam"
    return None


def upgrade_instructions() -> tuple[str, str]:
    if homebrew_command():
        return "Homebrew", "brew update\nbrew upgrade reunionstudio/tap/snowbeam"
    package = distribution("snowbeam")
    installer = (package.read_text("INSTALLER") or "").strip()
    direct = package.read_text("direct_url.json")
    if direct:
        # A local wheel/checkout must not silently switch to a registry package.
        try:
            json.loads(direct)
        except ValueError:
            return "Unknown", "Reinstall using the same method used to install Snowbeam."
        instructions = "Update your checkout or obtain the new release wheel."
        instructions += (
            " Then run:\nuv tool install --reinstall <checkout-or-wheel>"
            if installer == "uv"
            else " Reinstall it using the package manager for this Python environment."
        )
        return "Source or wheel", instructions
    if installer == "uv":
        return "uv", "uv tool upgrade snowbeam"
    return "Python environment", (
        "Upgrade Snowbeam using the package manager for this Python environment."
    )
