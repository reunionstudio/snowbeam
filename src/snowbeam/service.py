"""Coordinate independent connection checks while retaining previous evidence."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, Profile
from .preferences import Preferences
from .snowflake import SnowClient, SnowError
from .store import Store, stale


@dataclass
class RefreshResult:
    refreshed: int = 0
    issues: list[str] = field(default_factory=list)


class Service:
    def __init__(self, config: Config, store: Store, client: SnowClient | None = None):
        self.config = config
        self.store = store
        self.client = client or SnowClient()
        self.demo = False
        self.preferences = Preferences(store.directory)
        from .fleet_service import FleetService

        self.fleet = FleetService(self)

    def import_profiles(self) -> list[Profile]:
        profiles = self.config.profiles()
        self.store.sync_profiles(profiles, self.config.path)
        return profiles

    def refresh(
        self,
        name: str | None = None,
        *,
        interactive: bool = True,
        organization: bool = False,
        scheduled: bool = False,
    ) -> RefreshResult:
        profiles = self.import_profiles()
        if name:
            profiles = [self.config.profile(name)]
        result = RefreshResult()
        queried_orgs = set()
        for profile in profiles:
            minutes = self.preferences.refresh_minutes(profile.key)
            if scheduled and not self.store.refresh_due(profile.key, minutes):
                continue
            if not interactive and not profile.background_safe:
                result.issues.append(
                    f"{profile.name}: interactive sign-in required; cached data retained."
                )
                continue
            previous_issues = len(result.issues)
            completed = False
            try:
                try:
                    with self.fleet.session_for(profile, interactive=interactive) as actual:
                        identity, rows = self.client.snapshot(actual, interactive=interactive)
                    account_id = self.store.connected(profile, identity)
                except SnowError as exc:
                    if exc.code == "permission":
                        # A PAT inspection failure does not establish a broken login.
                        # Verify identity alone, but never combine tokens from another session.
                        try:
                            with self.fleet.session_for(profile, interactive=interactive) as actual:
                                identity = self.client.identity(actual, interactive=interactive)
                            account_id = self.store.connected(profile, identity)
                        except SnowError as identity_error:
                            self.store.failed(profile, identity_error.code, str(identity_error))
                            result.issues.append(f"{profile.name}: {identity_error}")
                            continue
                        result.refreshed += 1
                        self.store.tokens_failed(
                            account_id, str(identity["user_name"]), exc.code, str(exc)
                        )
                        result.issues.append(f"{profile.name}: PAT inventory: {exc}")
                        continue
                    self.store.failed(profile, exc.code, str(exc))
                    result.issues.append(f"{profile.name}: {exc}")
                    continue
                result.refreshed += 1
                user = str(identity["user_name"])
                self.store.save_tokens(account_id, user, rows)
                org = str(identity["organization_name"])
                if organization and org not in queried_orgs:
                    try:
                        with self.fleet.session_for(profile, interactive=interactive) as actual:
                            rows = self.client.accounts(actual, interactive=interactive)
                        self.store.save_accounts(org, rows)
                        queried_orgs.add(org)
                    except (SnowError, ValueError) as exc:
                        result.issues.append(f"{profile.name}: organization discovery: {exc}")
                completed = True
            finally:
                self.store.record_refresh(
                    profile.key,
                    minutes,
                    failed=not completed or len(result.issues) > previous_issues,
                )
        return result

    def refresh_due(self) -> bool:
        # Import first so a changed target loses its previous verification and schedule.
        return any(
            profile.background_safe
            and self.store.refresh_due(profile.key, self.preferences.refresh_minutes(profile.key))
            for profile in self.import_profiles()
        )

    def coverage_issues(self) -> list[str]:
        """An empty alert list must never imply an inventory we have not inspected."""
        issues = []
        connections = self.store.connections()
        checks = {(row["account_id"], row["user_name"]): row for row in self.store.pat_checks()}
        token_keys = {
            (row["account_id"], row["user_name"], row["name"]) for row in self.store.tokens()
        }
        if not connections:
            return ["No connections configured. Add a connection to begin."]
        for connection in connections:
            if connection["status"] != "ok":
                issues.append(f"{connection['name']}: {connection['message'] or 'not checked yet'}")
            elif stale(connection["checked_at"]):
                issues.append(f"{connection['name']}: connection check is over 24 hours old.")
            check = checks.get((connection["account_id"], connection["user_name"]))
            if not check:
                issues.append(f"{connection['name']}: PAT inventory has not been checked.")
            elif check["status"] != "ok":
                issues.append(
                    f"{connection['name']}: {check['message'] or 'PAT inventory unavailable'}"
                )
            elif stale(check["checked_at"]):
                issues.append(f"{connection['name']}: PAT inventory is over 24 hours old.")
            binding = (connection["account_id"], connection["user_name"], connection["token_name"])
            if connection["token_name"] and binding not in token_keys:
                issues.append(
                    f"{connection['name']}: associated PAT is no longer in the inventory."
                )
        return list(dict.fromkeys(issues))
