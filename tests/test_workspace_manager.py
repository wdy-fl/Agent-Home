from pathlib import Path

import pytest
from fastapi import HTTPException

from agent_home.config import Settings
from agent_home.workspace_manager import ensure_workspace, validate_workspace_agent_id, workspace_dir


def test_workspace_dir_uses_numeric_agent_id_directly(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "state.sqlite", workspace_root=tmp_path / "workspace")

    path = workspace_dir(settings, "001")

    assert path == tmp_path / "workspace" / "001"


def test_ensure_workspace_creates_agent_directory(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "state.sqlite", workspace_root=tmp_path / "workspace")

    path = ensure_workspace(settings, "123")

    assert path == tmp_path / "workspace" / "123"
    assert path.is_dir()


def test_ensure_workspace_reuses_existing_non_empty_directory(tmp_path: Path):
    settings = Settings(database_path=tmp_path / "state.sqlite", workspace_root=tmp_path / "workspace")
    existing = tmp_path / "workspace" / "123"
    existing.mkdir(parents=True)
    marker = existing / "kept.txt"
    marker.write_text("keep", encoding="utf-8")

    path = ensure_workspace(settings, "123")

    assert path == existing
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("agent_id", ["", "abc", "agent-1", "1/2", "../1", "1 2"])
def test_validate_workspace_agent_id_rejects_non_numeric_values(agent_id: str):
    with pytest.raises(HTTPException) as exc_info:
        validate_workspace_agent_id(agent_id)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"]["code"] == "invalid_agent_id"
