"""Check built distributions through clean installs and an isolated synthetic demo."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


async def check_installed(expected: str) -> None:
    from importlib.metadata import version
    from importlib.resources import files

    from textual.widgets import DataTable, TabbedContent

    import snowbeam
    from snowbeam.demo import demo_service
    from snowbeam.tui import Snowbeam

    assert Path(snowbeam.__file__).is_relative_to(Path(sys.prefix)), "Imported from checkout"
    assert version("snowbeam") == snowbeam.__version__ == expected, "Version mismatch"
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        icon = files("snowbeam").joinpath(f"assets/snowbeam-{size}.png").read_bytes()
        assert icon.startswith(b"\x89PNG\r\n\x1a\n"), f"Missing packaged icon: {size}"

    command = str(Path(sys.executable).with_name("snowbeam"))
    actual = run([command, "--version"], capture_output=True).stdout.strip()
    assert actual == f"Snowbeam {expected}", actual
    inventory = json.loads(
        run([command, "--demo", "inventory", "--json"], capture_output=True).stdout
    )
    assert inventory["accounts"] and inventory["connections"], "Demo inventory is empty"

    with tempfile.TemporaryDirectory(prefix="snowbeam-release-demo-") as temporary:
        app = Snowbeam(demo_service(Path(temporary)), auto_refresh=False)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert any(table.row_count for table in app.query(DataTable)), "Empty terminal UI"
            app.query_one(TabbedContent).active = "identities-tab"
            await pilot.pause()
            await pilot.press("q")
    print(f"Installed {expected}: version, icons, demo inventory, and terminal UI passed")


def check_archives(directory: Path, tag: str | None, prerelease: str | None) -> list[Path]:
    from packaging.version import Version

    project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    expected = project["project"]["version"]
    parsed_version = Version(expected)
    if tag is not None and tag != f"v{expected}":
        raise ValueError(f"Release tag must be v{expected}")
    if prerelease is not None and parsed_version.is_prerelease != (prerelease == "true"):
        raise ValueError("GitHub prerelease flag does not match the package version")

    wheel = directory / f"snowbeam-{expected}-py3-none-any.whl"
    source = directory / f"snowbeam-{expected}.tar.gz"
    if sorted(directory.glob("*.whl")) != [wheel] or sorted(directory.glob("*.tar.gz")) != [source]:
        raise ValueError("Expected exactly one wheel and one source archive for this version")

    with zipfile.ZipFile(wheel) as archive:
        prefix = f"snowbeam-{expected}.dist-info"
        metadata = BytesParser().parsebytes(archive.read(f"{prefix}/METADATA"))
        assert metadata["Name"] == "snowbeam" and metadata["Version"] == expected
        assert metadata["Requires-Python"] == ">=3.11"
        assert metadata["License-Expression"] == "MIT"
        assert archive.read(f"{prefix}/licenses/LICENSE").startswith(b"MIT License")

    with tarfile.open(source) as archive:
        members = {entry.name for entry in archive.getmembers()}
        for required in (
            "pyproject.toml",
            "LICENSE",
            "README.md",
            "uv.lock",
            "SECURITY.md",
            "src/snowbeam/cli.py",
            "tools/check_release.py",
            "docs/security.md",
        ):
            assert f"snowbeam-{expected}/{required}" in members, f"Source omits {required}"
        assert not any("/docs/brand/" in name or "/tools/brand/" in name for name in members)
        assert not any(
            name.endswith((".pem", ".p8", ".sqlite3", ".snowbeam.bak")) for name in members
        )

    # Run from outside the checkout with separate environments for both artifacts.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("SNOWFLAKE_")
    }
    for artifact in (wheel, source):
        with tempfile.TemporaryDirectory(prefix="snowbeam-release-install-") as temporary:
            destination = Path(temporary)
            venv = destination / "venv"
            run(["uv", "venv", "--python", sys.executable, str(venv)], env=environment)
            python = venv / "bin/python"
            run(["uv", "pip", "install", "--python", str(python), str(artifact)], env=environment)
            run(
                [str(python), str(Path(__file__).resolve()), "--installed-check", expected],
                cwd=destination,
                env=environment,
            )
            print(f"Verified {artifact.name}")
    return [wheel, source]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="dist", type=Path)
    parser.add_argument("--tag", help="Require this Git tag to match the package version")
    parser.add_argument("--prerelease", choices=("true", "false"))
    parser.add_argument("--installed-check", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.installed_check:
        asyncio.run(check_installed(args.installed_check))
        return
    directory = args.directory.resolve()
    artifacts = check_archives(directory, args.tag, args.prerelease)
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(artifacts)
    )
    (directory / "SHA256SUMS").write_text(checksums)
    print("Release package checks passed; wrote SHA256SUMS")


if __name__ == "__main__":
    main()
