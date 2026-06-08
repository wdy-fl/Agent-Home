from __future__ import annotations

import re
from pathlib import Path

from agent_home.config import Settings
from agent_home.errors import raise_error

_AGENT_ID_PATTERN = re.compile(r"^[0-9]+$")


def validate_workspace_agent_id(agent_id: str) -> str:
    if not _AGENT_ID_PATTERN.fullmatch(agent_id):
        raise_error("invalid_agent_id", "agent_id must contain digits only")
    return agent_id


def workspace_dir(settings: Settings, agent_id: str) -> Path:
    safe_agent_id = validate_workspace_agent_id(agent_id)
    return settings.workspace_root / safe_agent_id


def ensure_workspace(settings: Settings, agent_id: str) -> Path:
    path = workspace_dir(settings, agent_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
