from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("agent_home.sqlite")
    object_root: Path = Path(".agent-home/objects")
    execution_root: Path = Path(".agent-home/execution")


def default_settings() -> Settings:
    return Settings()
