"""Per-agent identities for Snowbeam agent context (challenge-response proofs)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import ConfigError, atomic_write

PROOF_TTL_SECONDS = 300
ENV_AGENT_ID = "SNOWBEAM_AGENT_ID"
ENV_AGENT_PROOF = "SNOWBEAM_AGENT_PROOF"


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    secret_fingerprint: str
    created_at: str
    revoked: bool = False


class AgentRegistry:
    def __init__(self, state_dir: Path):
        self.directory = state_dir.expanduser()
        self.registry_path = self.directory / "agents.json"
        self.secrets_dir = self.directory / "agents"
        self.pending_path = self.directory / "agents_pending.json"

    def _load_registry(self) -> dict[str, dict]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("Could not read agents.json.") from exc
        if not isinstance(data, dict):
            raise ConfigError("agents.json must be an object.")
        return data

    def _save_registry(self, data: dict[str, dict]) -> None:
        atomic_write(self.registry_path, json.dumps(data, indent=2, sort_keys=True))

    def _secret_path(self, agent_id: str) -> Path:
        return self.secrets_dir / f"{agent_id}.key"

    def _fingerprint(self, secret: bytes) -> str:
        return hashlib.sha256(secret).hexdigest()[:16]

    def list_agents(self) -> list[AgentRecord]:
        rows = []
        for agent_id, meta in sorted(self._load_registry().items()):
            if not isinstance(meta, dict):
                continue
            rows.append(
                AgentRecord(
                    agent_id=agent_id,
                    secret_fingerprint=str(meta.get("fingerprint", "")),
                    created_at=str(meta.get("created_at", "")),
                    revoked=bool(meta.get("revoked")),
                )
            )
        return rows

    def plan_register(self, agent_id: str) -> dict:
        return {
            "action": "register_agent",
            "agent_id": agent_id,
            "digest": hashlib.sha256(f"register:{agent_id}".encode()).hexdigest(),
        }

    def plan_revoke(self, agent_id: str) -> dict:
        return {
            "action": "revoke_agent",
            "agent_id": agent_id,
            "digest": hashlib.sha256(f"revoke:{agent_id}".encode()).hexdigest(),
        }

    def confirm_register(self, agent_id: str, *, approve_digest: str | None = None) -> AgentRecord:
        plan = self.plan_register(agent_id)
        if approve_digest is not None and approve_digest != plan["digest"]:
            raise ConfigError("Registration approval digest does not match.")
        if not approve_digest and not _human_confirmed(
            f"Register Snowbeam agent identity {agent_id!r}?"
        ):
            raise ConfigError("Agent registration was not confirmed.")
        secret = secrets.token_bytes(32)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        path = self._secret_path(agent_id)
        path.write_bytes(secret)
        os.chmod(path, 0o600)
        registry = self._load_registry()
        record = {
            "fingerprint": self._fingerprint(secret),
            "created_at": datetime.now(UTC).isoformat(),
            "revoked": False,
        }
        registry[agent_id] = record
        self._save_registry(registry)
        return AgentRecord(agent_id, record["fingerprint"], record["created_at"], False)

    def confirm_revoke(self, agent_id: str, *, approve_digest: str | None = None) -> None:
        plan = self.plan_revoke(agent_id)
        if approve_digest is not None and approve_digest != plan["digest"]:
            raise ConfigError("Revocation approval digest does not match.")
        if not approve_digest and not _human_confirmed(
            f"Revoke Snowbeam agent identity {agent_id!r}?"
        ):
            raise ConfigError("Agent revocation was not confirmed.")
        registry = self._load_registry()
        if agent_id not in registry:
            raise ConfigError(f"Unknown agent {agent_id!r}.")
        registry[agent_id]["revoked"] = True
        registry[agent_id]["revoked_at"] = datetime.now(UTC).isoformat()
        self._save_registry(registry)
        self._secret_path(agent_id).unlink(missing_ok=True)

    def _secret_for(self, agent_id: str) -> bytes:
        registry = self._load_registry()
        meta = registry.get(agent_id)
        if not meta or meta.get("revoked"):
            raise ConfigError(f"Agent {agent_id!r} is not registered.")
        path = self._secret_path(agent_id)
        if not path.is_file():
            raise ConfigError(f"Missing secret key file for agent {agent_id!r}.")
        return path.read_bytes()

    def mint_proof(self, agent_id: str, *, now: float | None = None) -> str:
        secret = self._secret_for(agent_id)
        now = int(now or time.time())
        nonce = secrets.token_hex(8)
        body = f"{agent_id}:{now}:{nonce}"
        sig = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        raw = f"{body}:{sig}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    def verify_proof(self, agent_id: str, proof: str, *, now: float | None = None) -> bool:
        try:
            padded = proof + "=" * (-len(proof) % 4)
            raw = base64.urlsafe_b64decode(padded.encode()).decode()
        except (ValueError, UnicodeDecodeError):
            return False
        parts = raw.split(":")
        if len(parts) != 4:
            return False
        proof_agent, issued, nonce, sig = parts
        if proof_agent != agent_id:
            return False
        try:
            issued_ts = int(issued)
        except ValueError:
            return False
        now_i = int(now or time.time())
        if now_i - issued_ts > PROOF_TTL_SECONDS or issued_ts > now_i + 30:
            return False
        try:
            secret = self._secret_for(agent_id)
        except ConfigError:
            return False
        body = f"{proof_agent}:{issued}:{nonce}"
        expected = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    def agent_context_from_environ(
        self, environ: dict[str, str] | None = None
    ) -> tuple[str, str] | None:
        env = environ or os.environ
        agent_id = env.get(ENV_AGENT_ID, "").strip()
        proof = env.get(ENV_AGENT_PROOF, "").strip()
        if not agent_id or not proof:
            return None
        if self.verify_proof(agent_id, proof):
            return agent_id, proof
        return None


def _human_confirmed(prompt: str) -> bool:
    from .tty_util import stdin_is_interactive_tty

    if not stdin_is_interactive_tty():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}
