from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    workspace_root = tmp_path / "workspace"
    app = create_app(Settings(database_path=tmp_path / "state.sqlite", workspace_root=workspace_root))
    return TestClient(app), workspace_root


def test_create_agent_returns_token_once_and_creates_workspace(tmp_path: Path):
    client, workspace_root = make_client(tmp_path)

    response = client.post("/v1/agents", json={"agent_id": "123"})

    assert response.status_code == 201
    body = response.json()
    assert body["agent_id"] == "123"
    assert body["token"]
    assert body["config"]["memory"]["auto_extract"]["enabled"] is False
    assert (workspace_root / "123").is_dir()


def test_create_agent_accepts_leading_zero_agent_id(tmp_path: Path):
    client, workspace_root = make_client(tmp_path)

    response = client.post("/v1/agents", json={"agent_id": "001"})

    assert response.status_code == 201
    assert response.json()["agent_id"] == "001"
    assert (workspace_root / "001").is_dir()
    assert not (workspace_root / "1").exists()


@pytest.mark.parametrize("agent_id", ["", "abc", "agent-1", "1/2", "../1", "1 2"])
def test_create_agent_rejects_non_numeric_agent_id(tmp_path: Path, agent_id: str):
    client, workspace_root = make_client(tmp_path)

    response = client.post("/v1/agents", json={"agent_id": agent_id})

    assert response.status_code == 422
    assert not workspace_root.exists()


def test_duplicate_agent_returns_agent_exists_and_reuses_workspace_directory(tmp_path: Path):
    client, workspace_root = make_client(tmp_path)
    first = client.post("/v1/agents", json={"agent_id": "123"})
    marker = workspace_root / "123" / "kept.txt"
    marker.write_text("keep", encoding="utf-8")

    second = client.post("/v1/agents", json={"agent_id": "123"})

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "agent_exists"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_get_agent_requires_matching_token(tmp_path: Path):
    client, _ = make_client(tmp_path)
    token = client.post("/v1/agents", json={"agent_id": "123"}).json()["token"]

    missing = client.get("/v1/agents/123")
    wrong = client.get("/v1/agents/123", headers={"Authorization": "Bearer bad"})
    ok = client.get("/v1/agents/123", headers={"Authorization": f"Bearer {token}"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth_failed"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "auth_failed"
    assert ok.status_code == 200
    assert ok.json()["agent_id"] == "123"


def test_agent_token_cannot_access_other_agent_home(tmp_path: Path):
    client, _ = make_client(tmp_path)
    token_456 = client.post("/v1/agents", json={"agent_id": "456"}).json()["token"]

    response = client.get("/v1/agents/123", headers={"Authorization": f"Bearer {token_456}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_failed"
