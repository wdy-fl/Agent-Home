from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path("agent_home.sqlite")
    workspace_root: Path = Path(".workspace")


def default_settings() -> Settings:
    return Settings()
