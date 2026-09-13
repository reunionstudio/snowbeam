"""Private, offline metadata cache. Credentials never enter this database."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from platformdirs import user_data_path

from .config import Profile
from .snowflake import timestamp


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(UTC).isoformat()


def stale(checked: str | None, now: datetime | None = None) -> bool:
    return not checked or (now or utcnow()) - datetime.fromisoformat(checked) > timedelta(hours=24)


def expiry_label(expires: str | None, now: datetime | None = None) -> str:
    if not expires:
        return "Unknown expiry"
    seconds = (datetime.fromisoformat(expires) - (now or utcnow())).total_seconds()
    if seconds <= 0:
        return "Expired"
    if seconds < 86400:
        return "Under 1 day"
    days = math.ceil(seconds / 86400)
    return f"In {days} {'day' if days == 1 else 'days'}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, organization TEXT NOT NULL, name TEXT NOT NULL,
    locator TEXT, region TEXT, url TEXT, checked_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'connection'
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, config_path TEXT NOT NULL,
    source_path TEXT NOT NULL, settings TEXT NOT NULL, is_default INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, account_id TEXT REFERENCES accounts(id),
    user_name TEXT, role_name TEXT, warehouse_name TEXT,
    status TEXT NOT NULL DEFAULT 'not_checked', message TEXT,
    attempted_at TEXT, checked_at TEXT, token_name TEXT
);
CREATE TABLE IF NOT EXISTS pat_checks (
    account_id TEXT NOT NULL REFERENCES accounts(id), user_name TEXT NOT NULL,
    attempted_at TEXT NOT NULL, checked_at TEXT, status TEXT NOT NULL, message TEXT,
    PRIMARY KEY(account_id, user_name)
);
CREATE TABLE IF NOT EXISTS tokens (
    account_id TEXT NOT NULL REFERENCES accounts(id), user_name TEXT NOT NULL, name TEXT NOT NULL,
    expires_at TEXT, status TEXT NOT NULL, role_restriction TEXT, created_on TEXT,
    checked_at TEXT NOT NULL, listed INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(account_id, user_name, name)
);
CREATE TABLE IF NOT EXISTS notices (key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_checks (
    organization TEXT PRIMARY KEY, checked_at TEXT NOT NULL, account_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS security_checks (
    account_id TEXT NOT NULL REFERENCES accounts(id), user_name TEXT NOT NULL,
    section TEXT NOT NULL, data TEXT, status TEXT NOT NULL, message TEXT,
    attempted_at TEXT NOT NULL, checked_at TEXT,
    PRIMARY KEY(account_id,user_name,section)
);
CREATE TABLE IF NOT EXISTS fleet_states (
    fleet_key TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY, fleet_key TEXT NOT NULL, action TEXT NOT NULL,
    target TEXT NOT NULL, status TEXT NOT NULL, steps TEXT NOT NULL,
    message TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_schedule (
    connection_id TEXT PRIMARY KEY, interval_minutes INTEGER NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0, next_attempt TEXT NOT NULL
);
PRAGMA user_version = 3;
"""


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = (directory or user_data_path("snowbeam", appauthor=False)).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "inventory.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Snowbeam's metadata database must not be a symlink.")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.db() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError("This cache was created by a newer Snowbeam. Upgrade Snowbeam.")
            db.executescript(SCHEMA)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def sync_profiles(self, profiles: list[Profile], config_path: Path) -> None:
        with self.db() as db:
            db.execute("UPDATE connections SET active=0 WHERE config_path=?", (str(config_path),))
            for profile in profiles:
                settings = json.dumps(profile.settings, sort_keys=True)
                old = db.execute(
                    "SELECT settings FROM connections WHERE id=?", (profile.key,)
                ).fetchone()
                if old and old["settings"] != settings:
                    db.execute("DELETE FROM refresh_schedule WHERE connection_id=?", (profile.key,))
                    # Changed settings must be re-verified, never inherit green status.
                    db.execute(
                        """UPDATE connections SET status='not_checked', message=NULL,
                               account_id=NULL, user_name=NULL, role_name=NULL, warehouse_name=NULL,
                               checked_at=NULL, attempted_at=NULL, token_name=NULL WHERE id=?""",
                        (profile.key,),
                    )
                db.execute(
                    """INSERT INTO connections
                    (id,name,config_path,source_path,settings,is_default) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                    source_path=excluded.source_path,
                    settings=excluded.settings, is_default=excluded.is_default, active=1""",
                    (
                        profile.key,
                        profile.name,
                        str(profile.config_path),
                        str(profile.source_path),
                        settings,
                        profile.is_default,
                    ),
                )

    def refresh_due(self, key: str, minutes: int, now: datetime | None = None) -> bool:
        if minutes == 0:
            return False
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM refresh_schedule WHERE connection_id=?", (key,)
            ).fetchone()
        return (
            not row
            or row["interval_minutes"] != minutes
            or (now or utcnow()) >= datetime.fromisoformat(row["next_attempt"])
        )

    def record_refresh(
        self, key: str, minutes: int, *, failed: bool, now: datetime | None = None
    ) -> None:
        with self.db() as db:
            row = db.execute(
                "SELECT failures FROM refresh_schedule WHERE connection_id=?", (key,)
            ).fetchone()
            failures = min((row["failures"] if row else 0) + 1, 4) if failed else 0
            delay = min(max(minutes, 1) * 2**failures, 1440)
            db.execute(
                """INSERT INTO refresh_schedule VALUES (?,?,?,?)
                ON CONFLICT(connection_id) DO UPDATE SET
                interval_minutes=excluded.interval_minutes, failures=excluded.failures,
                next_attempt=excluded.next_attempt""",
                (key, minutes, failures, iso((now or utcnow()) + timedelta(minutes=delay))),
            )

    @staticmethod
    def _account(db, data: dict, checked: str, source: str = "connection") -> str:
        organization = str(data["organization_name"])
        name = str(data["account_name"])
        locator = str(data.get("account_locator") or "")
        region = str(data.get("region") or data.get("snowflake_region") or "")
        existing = None
        if locator and region:
            existing = db.execute(
                "SELECT id FROM accounts WHERE locator=? AND region=?", (locator, region)
            ).fetchone()
        if not existing:
            existing = db.execute(
                "SELECT id FROM accounts WHERE organization=? AND name=?", (organization, name)
            ).fetchone()
        account_id = existing["id"] if existing else str(uuid.uuid4())
        db.execute(
            """INSERT INTO accounts
            (id,organization,name,locator,region,url,checked_at,source) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET organization=excluded.organization,name=excluded.name,
            locator=CASE WHEN excluded.locator!='' THEN excluded.locator ELSE accounts.locator END,
            region=CASE WHEN excluded.region!='' THEN excluded.region ELSE accounts.region END,
            url=COALESCE(excluded.url,accounts.url),checked_at=excluded.checked_at""",
            (
                account_id,
                organization,
                name,
                locator,
                region,
                data.get("account_url"),
                checked,
                source,
            ),
        )
        return account_id

    def connected(self, profile: Profile, identity: dict, now: datetime | None = None) -> str:
        checked = iso(now)
        with self.db() as db:
            account_id = self._account(db, identity, checked)
            old = db.execute(
                "SELECT account_id,user_name FROM connections WHERE id=?", (profile.key,)
            ).fetchone()
            if old and (
                old["account_id"] != account_id or old["user_name"] != identity["user_name"]
            ):
                db.execute("UPDATE connections SET token_name=NULL WHERE id=?", (profile.key,))
            db.execute(
                """UPDATE connections SET account_id=?, user_name=?, role_name=?,
                warehouse_name=?, status='ok', message=NULL,
                attempted_at=?, checked_at=? WHERE id=?""",
                (
                    account_id,
                    identity["user_name"],
                    identity.get("role_name"),
                    identity.get("warehouse_name"),
                    checked,
                    checked,
                    profile.key,
                ),
            )
        return account_id

    def failed(
        self, profile: Profile, code: str, message: str, now: datetime | None = None
    ) -> None:
        with self.db() as db:
            db.execute(
                "UPDATE connections SET status=?,message=?,attempted_at=? WHERE id=?",
                (code, message, iso(now), profile.key),
            )

    def save_tokens(
        self, account_id: str, user: str, rows: list[dict], now: datetime | None = None
    ) -> None:
        checked = iso(now)
        # Validate the complete response before changing any cached rows.
        if any(str(row.get("user_name")) != user for row in rows):
            raise ValueError("Token inventory does not match the authenticated user.")
        with self.db() as db:
            db.execute(
                "UPDATE tokens SET listed=0 WHERE account_id=? AND user_name=?", (account_id, user)
            )
            for row in rows:
                db.execute(
                    """INSERT INTO tokens
                    (account_id,user_name,name,expires_at,status,role_restriction,created_on,checked_at)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(account_id,user_name,name) DO UPDATE SET
                    expires_at=excluded.expires_at,status=excluded.status,
                    role_restriction=excluded.role_restriction,created_on=excluded.created_on,
                    checked_at=excluded.checked_at,listed=1""",
                    (
                        account_id,
                        user,
                        str(row["name"]),
                        timestamp(row.get("expires_at")),
                        str(row.get("status") or "UNKNOWN").upper(),
                        row.get("role_restriction"),
                        timestamp(row.get("created_on")),
                        checked,
                    ),
                )
            db.execute(
                """INSERT INTO pat_checks
                (account_id,user_name,attempted_at,checked_at,status) VALUES (?,?,?,?,'ok')
                ON CONFLICT(account_id,user_name) DO UPDATE SET attempted_at=excluded.attempted_at,
                checked_at=excluded.checked_at,status='ok',message=NULL""",
                (account_id, user, checked, checked),
            )

    def tokens_failed(
        self, account_id: str, user: str, code: str, message: str, now: datetime | None = None
    ) -> None:
        with self.db() as db:
            db.execute(
                """INSERT INTO pat_checks
                (account_id,user_name,attempted_at,status,message) VALUES (?,?,?,?,?)
                ON CONFLICT(account_id,user_name) DO UPDATE SET attempted_at=excluded.attempted_at,
                status=excluded.status,message=excluded.message""",
                (account_id, user, iso(now), code, message),
            )

    def save_accounts(
        self, organization: str, rows: list[dict], now: datetime | None = None
    ) -> None:
        checked = iso(now)
        if any(str(row["organization_name"]) != organization for row in rows):
            raise ValueError("Account inventory does not match the authenticated organization.")
        with self.db() as db:
            for row in rows:
                self._account(db, row, checked, "organization")
            db.execute(
                "INSERT OR REPLACE INTO org_checks VALUES (?,?,?)",
                (organization, checked, len(rows)),
            )

    def accounts(self) -> list[dict]:
        with self.db() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM accounts ORDER BY organization,name")
            ]

    def connections(self) -> list[dict]:
        with self.db() as db:
            rows = db.execute("""SELECT c.*,a.organization,a.name AS account_name,a.locator,a.region
                FROM connections c LEFT JOIN accounts a ON a.id=c.account_id
                WHERE c.active=1 ORDER BY c.name""").fetchall()
            result = [dict(row) for row in rows]
        for row in result:
            row["settings"] = json.loads(row["settings"])
        return result

    def tokens(self, *, include_unlisted: bool = False) -> list[dict]:
        with self.db() as db:
            return [
                dict(row)
                for row in db.execute(
                    """SELECT t.*,a.organization,a.name AS account_name,
                pc.status AS inventory_status,pc.message AS inventory_message
                FROM tokens t JOIN accounts a ON a.id=t.account_id
                LEFT JOIN pat_checks pc ON pc.account_id=t.account_id AND pc.user_name=t.user_name
                WHERE (? OR t.listed=1) ORDER BY t.expires_at IS NULL,t.expires_at,t.name""",
                    (include_unlisted,),
                )
            ]

    def pat_checks(self) -> list[dict]:
        with self.db() as db:
            return [
                dict(row)
                for row in db.execute("""SELECT p.*,a.organization,a.name AS account_name
                FROM pat_checks p JOIN accounts a ON a.id=p.account_id""")
            ]

    def bind_token(self, profile_key: str, token_name: str | None) -> None:
        with self.db() as db:
            connection = db.execute(
                "SELECT * FROM connections WHERE id=?", (profile_key,)
            ).fetchone()
            if not connection or not connection["account_id"]:
                raise ValueError("Refresh this connection before associating a token.")
            if (
                token_name
                and not db.execute(
                    """SELECT 1 FROM tokens WHERE account_id=?
                AND user_name=? AND name=? AND listed=1""",
                    (connection["account_id"], connection["user_name"], token_name),
                ).fetchone()
            ):
                raise ValueError("Choose a token from this connection's account and user.")
            db.execute("UPDATE connections SET token_name=? WHERE id=?", (token_name, profile_key))

    def alerts(self, *, days: int = 14, now: datetime | None = None) -> list[dict]:
        now = now or utcnow()
        alerts = []
        connections = self.connections()
        for token in self.tokens():
            expires = token["expires_at"]
            remaining = (
                None if not expires else (datetime.fromisoformat(expires) - now).total_seconds()
            )
            if token["status"] == "ACTIVE" and remaining is not None and remaining > days * 86400:
                continue
            token = dict(token)
            token["expiry_label"] = expiry_label(expires, now)
            token["stale"] = stale(token["checked_at"], now)
            token["connections"] = [
                c["name"]
                for c in connections
                if c["account_id"] == token["account_id"]
                and c["user_name"] == token["user_name"]
                and c["token_name"] == token["name"]
            ]
            if remaining is None:
                bucket = "unknown"
            elif remaining <= 0:
                bucket = "expired"
            else:
                bucket = str(next((d for d in (1, 3, 7, 14) if remaining <= d * 86400), days))
            token["notice_key"] = json.dumps(
                [
                    token["account_id"],
                    token["user_name"],
                    token["name"],
                    expires,
                    token["status"],
                    bucket,
                ]
            )
            alerts.append(token)
        return alerts

    def notice_sent(self, key: str) -> bool:
        with self.db() as db:
            return db.execute("SELECT 1 FROM notices WHERE key=?", (key,)).fetchone() is not None

    def mark_notice(self, key: str) -> None:
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO notices VALUES (?,?)", (key, iso()))

    def ensure_account(self, identity: dict) -> str:
        with self.db() as db:
            return self._account(db, identity, iso())

    def save_security(self, account_id: str, user: str, sections: dict) -> None:
        from .security import sanitize_section

        now = iso()
        with self.db() as db:
            for section, result in sections.items():
                if result["status"] == "ok":
                    data = json.dumps(sanitize_section(section, result["data"]), sort_keys=True)
                    db.execute(
                        """INSERT INTO security_checks
                        (account_id,user_name,section,data,status,attempted_at,checked_at)
                        VALUES (?,?,?,?,'ok',?,?) ON CONFLICT(account_id,user_name,section)
                        DO UPDATE SET data=excluded.data,status='ok',message=NULL,
                        attempted_at=excluded.attempted_at,checked_at=excluded.checked_at""",
                        (account_id, user, section, data, now, now),
                    )
                else:
                    db.execute(
                        """INSERT INTO security_checks
                        (account_id,user_name,section,status,message,attempted_at)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(account_id,user_name,section)
                        DO UPDATE SET status=excluded.status,message=excluded.message,
                        attempted_at=excluded.attempted_at""",
                        (account_id, user, section, result["status"], result.get("message"), now),
                    )

    def security(self, account_id: str, user: str) -> dict:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM security_checks WHERE account_id=? AND user_name=?",
                (account_id, user),
            ).fetchall()
        result = {}
        for row in rows:
            value = dict(row)
            value["data"] = json.loads(value["data"]) if value["data"] else None
            value["stale"] = stale(value["checked_at"])
            result[value.pop("section")] = value
        return result

    def fleet_state(self, key: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT data FROM fleet_states WHERE fleet_key=?", (key,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def fleet_states(self) -> dict:
        with self.db() as db:
            rows = db.execute("SELECT fleet_key, data FROM fleet_states").fetchall()
        return {row["fleet_key"]: json.loads(row["data"]) for row in rows}

    def save_fleet_state(self, key: str, state: dict) -> None:
        allowed = {
            "target",
            "status",
            "credential_name",
            "credential_kind",
            "credential_ref",
            "pending_name",
            "pending_ref",
            "pending_fingerprint",
            "public_key_fp",
            "key_slot",
            "pending_slot",
            "previous_slot",
            "previous_name",
            "previous_ref",
            "previous_fingerprint",
            "message",
            "runtime_verified_at",
            "verification_source",
            "runtime_ip",
            "runtime_role",
            "operation_id",
            "created_user",
            "workload",
            "approved_policy_digest",
            "approved_role_digest",
        }
        if set(state) - allowed:
            raise ValueError("Unknown lifecycle fields. Credentials cannot be stored in the cache.")
        from .fleet import REFERENCE_FIELDS

        for field in ("credential_ref", "pending_ref", "previous_ref"):
            if state.get(field) and set(state[field]) - REFERENCE_FIELDS:
                raise ValueError("Only vault references can be cached.")
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO fleet_states VALUES (?,?,?)",
                (key, json.dumps(state, sort_keys=True), iso()),
            )

    def save_operation(
        self,
        operation_id: str,
        key: str,
        action: str,
        target: dict,
        status: str,
        steps: list[str],
        message: str = "",
    ) -> None:
        now = iso()
        with self.db() as db:
            db.execute(
                """INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,steps=excluded.steps,
                message=excluded.message,updated_at=excluded.updated_at""",
                (
                    operation_id,
                    key,
                    action,
                    json.dumps(target, sort_keys=True),
                    status,
                    json.dumps(steps),
                    message,
                    now,
                    now,
                ),
            )

    def operations(self, key: str | None = None) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM operations WHERE (? IS NULL OR fleet_key=?) ORDER BY "
                "created_at DESC LIMIT 100",
                (key, key),
            ).fetchall()
        result = [dict(r) for r in rows]
        for row in result:
            row["steps"] = json.loads(row["steps"])
            row["target"] = json.loads(row["target"])
        return result
