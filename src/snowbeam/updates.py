"""Optional public release checks. Never download or execute application code."""

import json
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from packaging.version import InvalidVersion, Version

from . import __version__
from .config import atomic_write
from .store import iso, utcnow

RELEASES_URL = "https://api.github.com/repos/reunionstudio/snowbeam/releases?per_page=30"
RELEASES_PAGE = "https://github.com/reunionstudio/snowbeam/releases"
MAX_RESPONSE = 256 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError("Release endpoint redirected")


def latest_version(releases: object, current: str = __version__) -> str | None:
    if not isinstance(releases, list):
        raise ValueError("Invalid release list")
    installed = Version(current)
    versions = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") is not False:
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or len(tag) > 64:
            continue
        try:
            candidate = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if candidate.local or candidate.is_devrelease:
            continue
        if not installed.is_prerelease and (candidate.is_prerelease or release.get("prerelease")):
            continue
        versions.append(candidate)
    return str(max(versions)) if versions else None


class Updates:
    def __init__(self, directory: Path):
        self.path = directory / "updates.json"

    def status(self) -> dict:
        try:
            cached = json.loads(self.path.read_text())
            if not isinstance(cached, dict):
                raise ValueError("Invalid cache")
            latest = cached.get("latest")
            if latest is not None:
                latest = str(Version(latest))
                if not Version(__version__).is_prerelease and Version(latest).is_prerelease:
                    latest = None
            checked = cached.get("checked_at")
            attempted = cached.get("attempted_at")
            for value in (checked, attempted):
                if value is not None:
                    from datetime import datetime

                    if datetime.fromisoformat(value).tzinfo is None:
                        raise ValueError("Missing timezone")
            error = cached.get("error")
            if error not in (None, "unavailable", "no_release"):
                raise ValueError("Invalid error status")
        except (OSError, ValueError, TypeError):
            latest = checked = attempted = error = None
        return {
            "installed": __version__,
            "latest": latest,
            "available": bool(latest and Version(latest) > Version(__version__)),
            "checked_at": checked,
            "attempted_at": attempted,
            "error": error,
            "releases_url": RELEASES_PAGE,
        }

    def due(self) -> bool:
        from datetime import datetime

        attempted = self.status()["attempted_at"]
        return not attempted or utcnow() - datetime.fromisoformat(attempted) >= timedelta(days=1)

    def check(self) -> dict:
        cached = self.status()
        cached["attempted_at"] = iso()
        request = Request(
            RELEASES_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Snowbeam-updates"},
        )
        try:
            # No GitHub token, Snowflake configuration, or vault context is attached.
            with build_opener(NoRedirect()).open(request, timeout=5) as response:
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise ValueError("Release response too large")
                latest = latest_version(json.loads(data))
            cached.update(latest=latest, checked_at=iso(), error=None if latest else "no_release")
        except HTTPError as exc:
            cached["error"] = "no_release" if exc.code == 404 else "unavailable"
        except (OSError, URLError, ValueError):
            cached["error"] = "unavailable"
        atomic_write(self.path, json.dumps(cached, indent=2) + "\n")
        return self.status()


def describe(status: dict) -> str:
    lines = [f"Installed: {status['installed']}"]
    if status["available"]:
        lines.append(f"Update available: {status['latest']}")
    elif status["latest"]:
        lines.append(f"Latest release in this channel: {status['latest']}")
    else:
        lines.append("No published release has been verified.")
    if status["checked_at"]:
        lines.append(f"Last verified: {status['checked_at']}")
    if status["error"]:
        lines.append("Release check unavailable. Previous results may be out of date.")
    lines.append("Alpha installs include prereleases; stable installs check stable releases.")
    return "\n".join(lines)
