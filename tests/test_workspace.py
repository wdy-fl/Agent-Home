from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings
from agent_home.workspace import object_root


def make_client(tmp_path: Path, agent_id: str = "agent-a"):
    client = TestClient(create_app(Settings(database_path=tmp_path / "state.sqlite", object_root=tmp_path / "objects")))
    token = client.post("/v1/agents", json={"agent_id": agent_id}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_workspace_crud_and_list(tmp_path: Path):
    client, headers = make_client(tmp_path)

    created = client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"}, content=b"print('hi')")
    read = client.get("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"})
    listed = client.get("/v1/agents/agent-a/workspace/objects", headers=headers, params={"prefix": "/src"})
    deleted = client.delete("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"})
    missing = client.get("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"})

    assert created.status_code == 200
    assert created.json()["path"] == "/src/main.py"
    assert read.content == b"print('hi')"
    assert listed.json()[0]["path"] == "/src/main.py"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "path_not_found"


def test_invalid_path_rejected(tmp_path: Path):
    client, headers = make_client(tmp_path)

    response = client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "../secret"}, content=b"bad")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_path"


def test_agent_id_cannot_escape_object_root(tmp_path: Path):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings(object_root=tmp_path / "objects"))))

    root = object_root(request, "../evil")

    assert root.parent == tmp_path / "objects"
    assert root != tmp_path / "objects" / "../evil"
    assert not (tmp_path / "evil").exists()


def test_object_root_uses_safe_agent_directory(tmp_path: Path):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=Settings(object_root=tmp_path / "objects"))))

    root = object_root(request, "a/b")

    assert root.parent == tmp_path / "objects"
    assert root != tmp_path / "objects" / "a/b"
    assert "a/b" not in root.as_posix()


def test_object_replacement_returns_new_content_and_removes_old_blob(tmp_path: Path):
    client, headers = make_client(tmp_path)

    first = client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"}, content=b"old")
    old_blob = next((tmp_path / "objects").rglob("*.blob"))
    second = client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"}, content=b"new")
    read = client.get("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert read.content == b"new"
    assert not old_blob.exists()
    assert len(list((tmp_path / "objects").rglob("*.blob"))) == 1


def test_delete_removes_blob_from_disk(tmp_path: Path):
    client, headers = make_client(tmp_path)

    client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"}, content=b"data")
    blob = next((tmp_path / "objects").rglob("*.blob"))
    response = client.delete("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/src/main.py"})

    assert response.status_code == 204
    assert not blob.exists()


def test_path_with_traversal_part_rejected(tmp_path: Path):
    client, headers = make_client(tmp_path)

    response = client.put("/v1/agents/agent-a/workspace/object", headers=headers, params={"path": "/a/../b"}, content=b"bad")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_path"


def test_object_larger_than_limit_rejected(tmp_path: Path):
    client, headers = make_client(tmp_path)

    response = client.put(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/large.bin"},
        content=b"x" * 1_000_001,
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "object_too_large"
