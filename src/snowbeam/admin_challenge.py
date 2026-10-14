"""Human-only admin unlock with rate limiting and non-revealing agent failures."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .os_secrets import SecretStore
from .tty_util import stdin_is_interactive_tty, tty_getpass

GENERIC_SNOW_FAILURE = 1
DECOY_ENV_VAR = "PRIVATE_KEY_PASSPHRASE"


class AdminAccessDenied(Exception):
    """Non-revealing failure for agents or lockout; no secret values in messages."""

    exit_code: int = GENERIC_SNOW_FAILURE


@dataclass(frozen=True)
class LockoutState:
    failures: int = 0
    locked_until: float | None = None


class AdminLockoutStore:
    """Owner-only lockout file with an HMAC over the payload."""

    def __init__(self, state_dir: Path, *, key_path: Path | None = None):
        self.path = state_dir.expanduser() / "admin_lockout.json"
        self.key_path = key_path or state_dir.expanduser() / ".admin_lockout.key"

    def _key(self) -> bytes:
        if not self.key_path.exists():
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            material = secrets.token_bytes(32)
            self.key_path.write_bytes(material)
            os.chmod(self.key_path, 0o600)
            return material
        return self.key_path.read_bytes()

    def _sign(self, payload: dict) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._key(), body, hashlib.sha256).hexdigest()

    def read(self) -> LockoutState:
        if not self.path.exists():
            return LockoutState()
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return LockoutState()
        payload = raw.get("payload")
        signature = raw.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature, str):
            return LockoutState()
        if self._sign(payload) != signature:
            return LockoutState(failures=99, locked_until=time.time() + 3600)
        locked = payload.get("locked_until")
        return LockoutState(
            failures=int(payload.get("failures", 0)),
            locked_until=float(locked) if locked is not None else None,
        )

    def write(self, state: LockoutState) -> None:
        payload = {
            "failures": state.failures,
            "locked_until": state.locked_until,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        document = {"payload": payload, "signature": self._sign(payload)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(document, indent=2))
        os.chmod(self.path, 0o600)

    def is_locked(self, now: float | None = None) -> bool:
        state = self.read()
        if state.locked_until is None:
            return False
        return (now or time.time()) < state.locked_until

    def record_failure(self, now: float | None = None) -> LockoutState:
        now = now or time.time()
        state = self.read()
        if state.locked_until and now < state.locked_until:
            return state
        failures = state.failures + 1
        locked_until = None
        if failures >= 2:
            backoff = min(3600, 30 * (2 ** (failures - 2)))
            locked_until = now + backoff
        new_state = LockoutState(failures=failures, locked_until=locked_until)
        self.write(new_state)
        return new_state

    def record_success(self) -> None:
        self.write(LockoutState())


def decoy_passphrase_for_connection(connection: str) -> str:
    digest = hashlib.sha256(f"snowbeam-decoy:{connection}".encode()).hexdigest()[:24]
    return f"decoy-{digest}"


def apply_decoy_to_child_env(child: dict[str, str], connection: str) -> dict[str, str]:
    child[DECOY_ENV_VAR] = decoy_passphrase_for_connection(connection)
    return child


def unlock_admin_passphrase(
    connection: str,
    secret_store: SecretStore,
    lockout: AdminLockoutStore,
    *,
    silent_on_denial: bool,
    tty_getpass_fn=tty_getpass,
    now: float | None = None,
) -> str | None:
    """
    Return the real passphrase for an admin connection after human verification.

    When silent_on_denial is True (typical agent path), raise AdminAccessDenied
    without printing hints. Humans on a TTY are prompted; Keychain is tried first
    when the store returns a value without UI, then typed passphrase fallback.
    """
    if lockout.is_locked(now):
        if silent_on_denial:
            raise AdminAccessDenied()
        raise AdminAccessDenied()

    stored = secret_store.get_passphrase(connection)
    if stored and not stdin_is_interactive_tty():
        if silent_on_denial:
            raise AdminAccessDenied()
        raise AdminAccessDenied()

    if stored and stdin_is_interactive_tty():
        typed = tty_getpass_fn(f"Unlock admin connection {connection} (passphrase): ")
        if typed is None:
            lockout.record_failure(now)
            if silent_on_denial:
                raise AdminAccessDenied()
            raise AdminAccessDenied()
        if typed != stored:
            lockout.record_failure(now)
            if silent_on_denial:
                raise AdminAccessDenied()
            raise AdminAccessDenied()
        lockout.record_success()
        return stored

    if stdin_is_interactive_tty():
        typed = tty_getpass_fn(f"Passphrase for admin connection {connection}: ")
        if typed is None:
            lockout.record_failure(now)
            raise AdminAccessDenied()
        if not typed:
            lockout.record_failure(now)
            raise AdminAccessDenied()
        lockout.record_success()
        return typed

    if silent_on_denial:
        raise AdminAccessDenied()
    raise AdminAccessDenied()
