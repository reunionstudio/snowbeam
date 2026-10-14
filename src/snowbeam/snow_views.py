"""Filtered Snowflake CLI config directories per access group."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import tomlkit

from .config import Config, read_document
from .connection_access import CLASS_ADMIN, CLASS_AGENT, ConnectionAccess


def view_root(state_dir: Path) -> Path:
    return state_dir.expanduser() / "snow-views"


def view_home(state_dir: Path, group: str) -> Path:
    if group not in {CLASS_ADMIN, CLASS_AGENT}:
        raise ValueError(f"Unknown snow view group {group!r}.")
    return view_root(state_dir) / group


def materialize_view(
    master: Config,
    access: ConnectionAccess,
    state_dir: Path,
    *,
    group: str,
) -> Path:
    """Write config.toml + connections.toml containing only connections for this group."""
    home = view_home(state_dir, group)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    master_names = [p.name for p in master.profiles(environment=False)]
    if group == CLASS_AGENT:
        names = set(access.visible_connection_names(master_names, agent_context=True))
    else:
        names = set(master_names)
    source = master.source
    entries = read_document(source)
    if source == master.path:
        raw = entries.get("connections", {})
    else:
        raw = entries
    if not isinstance(raw, dict):
        raw = {}
    filtered = tomlkit.table()
    for name, values in raw.items():
        if name in names and isinstance(values, dict):
            filtered[name] = copy.deepcopy(values)
    conn_path = home / "connections.toml"
    atomic = tomlkit.dumps(filtered)
    conn_path.write_text(atomic)
    os.chmod(conn_path, 0o600)
    main_doc = read_document(master.path)
    view_main = tomlkit.document()
    default = main_doc.get("default_connection_name")
    if default in names:
        view_main["default_connection_name"] = default
    elif names:
        view_main["default_connection_name"] = sorted(names, key=str.casefold)[0]
    main_path = home / "config.toml"
    main_path.write_text(tomlkit.dumps(view_main))
    os.chmod(main_path, 0o600)
    return home


def config_for_run(
    master: Config,
    access: ConnectionAccess,
    state_dir: Path,
    *,
    agent_context: bool,
) -> Config:
    group = CLASS_AGENT if agent_context else CLASS_ADMIN
    home = materialize_view(master, access, state_dir, group=group)
    return Config(home / "config.toml")
