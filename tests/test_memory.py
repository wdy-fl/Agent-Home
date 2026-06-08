from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path):
    client = TestClient(create_app(Settings(database_path=tmp_path / "state.sqlite", workspace_root=tmp_path / "workspace")))
    token = client.post("/v1/agents", json={"agent_id": "123"}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_manual_memory_crud_and_search(tmp_path: Path):
    client, headers = make_client(tmp_path)

    created = client.post("/v1/agents/123/memory", headers=headers, json={"type": "preference", "content": "Use python3 for Python commands", "tags": ["python"]}).json()
    memory_id = created["memory_id"]
    found = client.get("/v1/agents/123/memory/search", headers=headers, params={"q": "python3"}).json()
    updated = client.patch(f"/v1/agents/123/memory/{memory_id}", headers=headers, json={"content": "Always use python3"}).json()
    deleted = client.delete(f"/v1/agents/123/memory/{memory_id}", headers=headers)

    assert found[0]["memory_id"] == memory_id
    assert updated["content"] == "Always use python3"
    assert deleted.status_code == 204


def test_deleted_memory_is_not_readable_or_searchable(tmp_path: Path):
    client, headers = make_client(tmp_path)
    created = client.post("/v1/agents/123/memory", headers=headers, json={"type": "preference", "content": "Delete me from search", "tags": []}).json()
    memory_id = created["memory_id"]

    deleted = client.delete(f"/v1/agents/123/memory/{memory_id}", headers=headers)
    fetched = client.get(f"/v1/agents/123/memory/{memory_id}", headers=headers)
    found = client.get("/v1/agents/123/memory/search", headers=headers, params={"q": "Delete me"}).json()

    assert deleted.status_code == 204
    assert fetched.status_code == 404
    assert fetched.json()["error"]["code"] == "memory_not_found"
    assert found == []


def test_memory_create_and_search_reject_missing_and_wrong_bearer_tokens(tmp_path: Path):
    client, headers = make_client(tmp_path)
    created = client.post("/v1/agents/123/memory", headers=headers, json={"type": "preference", "content": "Token protected memory", "tags": []}).json()

    requests = [
        client.post("/v1/agents/123/memory", json={"type": "preference", "content": "No token", "tags": []}),
        client.post("/v1/agents/123/memory", headers={"Authorization": "Bearer wrong-token"}, json={"type": "preference", "content": "Wrong token", "tags": []}),
        client.get("/v1/agents/123/memory/search", params={"q": created["content"]}),
        client.get("/v1/agents/123/memory/search", headers={"Authorization": "Bearer wrong-token"}, params={"q": created["content"]}),
    ]

    for response in requests:
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth_failed"


def test_patch_status_deleted_is_ignored_and_memory_stays_active(tmp_path: Path):
    client, headers = make_client(tmp_path)
    created = client.post("/v1/agents/123/memory", headers=headers, json={"type": "preference", "content": "Status patch should not delete", "tags": ["status"]}).json()
    memory_id = created["memory_id"]

    patched = client.patch(f"/v1/agents/123/memory/{memory_id}", headers=headers, json={"status": "deleted"})
    fetched = client.get(f"/v1/agents/123/memory/{memory_id}", headers=headers)
    found = client.get("/v1/agents/123/memory/search", headers=headers, params={"q": "Status patch"}).json()

    assert patched.status_code == 200
    assert patched.json()["status"] == "active"
    assert patched.json()["content"] == "Status patch should not delete"
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "active"
    assert found[0]["memory_id"] == memory_id


def test_extraction_disabled_by_default(tmp_path: Path):
    client, headers = make_client(tmp_path)

    response = client.post("/v1/agents/123/memory/extractions", headers=headers, json={"session_id": "s1", "trigger": "after_agent"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "auto_extract_disabled"
