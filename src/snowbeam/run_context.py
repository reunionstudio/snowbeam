"""Resolve whether Snowbeam is running in human or authenticated agent context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_registry import AgentRegistry


@dataclass(frozen=True)
class RunContext:
    agent_context: bool
    agent_id: str | None = None

    @property
    def silent_admin_denial(self) -> bool:
        return self.agent_context


def resolve_run_context(
    state_dir: Path,
    *,
    agent_id: str | None = None,
    agent_proof: str | None = None,
    environ: dict[str, str] | None = None,
) -> RunContext:
    registry = AgentRegistry(state_dir)
    if agent_id and agent_proof:
        if registry.verify_proof(agent_id, agent_proof):
            return RunContext(agent_context=True, agent_id=agent_id)
        return RunContext(agent_context=True, agent_id=None)
    pair = registry.agent_context_from_environ(environ)
    if pair:
        return RunContext(agent_context=True, agent_id=pair[0])
    return RunContext(agent_context=False, agent_id=None)
