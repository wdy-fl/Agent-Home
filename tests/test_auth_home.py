from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(database_path=tmp_path / "state.sqlite", object_root=tmp_path / "objects"))
    return TestClient(app)


def test_create_agent_returns_token_once(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/v1/agents", json={"agent_id": "agent-a"})

    assert response.status_code == 201
    body = response.json()
    assert body["agent_id"] == "agent-a"
    assert body["token"]
    assert body["config"]["memory"]["auto_extract"]["enabled"] is False


def test_get_agent_requires_matching_token(tmp_path: Path):
    client = make_client(tmp_path)
    token = client.post("/v1/agents", json={"agent_id": "agent-a"}).json()["token"]

    missing = client.get("/v1/agents/agent-a")
    wrong = client.get("/v1/agents/agent-a", headers={"Authorization": "Bearer bad"})
    ok = client.get("/v1/agents/agent-a", headers={"Authorization": f"Bearer {token}"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth_failed"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "auth_failed"
    assert ok.status_code == 200
    assert ok.json()["agent_id"] == "agent-a"


def test_agent_token_cannot_access_other_agent_home(tmp_path: Path):
    client = make_client(tmp_path)
    token_b = client.post("/v1/agents", json={"agent_id": "agent-b"}).json()["token"]

    response = client.get("/v1/agents/agent-a", headers={"Authorization": f"Bearer {token_b}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth_failed"
