import hashlib
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings
from agent_home.workspace import safe_agent_dir


def make_client_with_app(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path, FastAPI]:
    app = create_app(
        Settings(
            database_path=tmp_path / "state.sqlite",
            object_root=tmp_path / "objects",
            execution_root=tmp_path / "exec",
        )
    )
    client = TestClient(app)
    token = client.post("/v1/agents", json={"agent_id": "agent-a"}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}, tmp_path, app


def make_client(tmp_path: Path) -> tuple[TestClient, dict[str, str], Path]:
    client, headers, root, _ = make_client_with_app(tmp_path)
    return client, headers, root


def test_workspace_command_reads_existing_logical_file_and_writes_back_changes(tmp_path: Path):
    client, headers, root = make_client(tmp_path)
    client.put(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
        content=b"before",
    )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "cat notes/todo.md && printf '\nafter' >> notes/todo.md", "timeout_seconds": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["exit_code"] == 0
    assert "before" in body["stdout"]
    assert body["changed_paths"] == ["/notes/todo.md"]
    updated = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
    )
    assert updated.text == "before\nafter"
    assert not (root / "notes" / "todo.md").exists()


def test_workspace_command_new_file_is_synced_to_logical_workspace(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "mkdir -p artifacts && printf '{\"ok\": true}' > artifacts/result.json"},
    )

    assert response.status_code == 200
    assert response.json()["changed_paths"] == ["/artifacts/result.json"]
    read = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/artifacts/result.json"},
    )
    assert read.text == '{"ok": true}'


def test_workspace_command_nonzero_exit_still_syncs_changes_and_returns_result(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)
    client.put(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
        content=b"before",
    )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={
            "command": "printf '\nafter' >> notes/todo.md; mkdir -p reports; printf failed > reports/status.txt; exit 7",
            "timeout_seconds": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["exit_code"] == 7
    assert body["changed_paths"] == ["/notes/todo.md", "/reports/status.txt"]
    updated = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
    )
    created = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/reports/status.txt"},
    )
    assert updated.text == "before\nafter"
    assert created.text == "failed"


def test_workspace_command_materializes_only_under_agent_execution_root(tmp_path: Path):
    client, headers, root = make_client(tmp_path)
    client.put(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
        content=b"before",
    )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "test -f notes/todo.md && printf generated > generated.txt"},
    )

    agent_dir = safe_agent_dir("agent-a")
    assert response.status_code == 200
    assert (root / "exec" / agent_dir / "notes" / "todo.md").read_text() == "before"
    assert (root / "exec" / agent_dir / "generated.txt").read_text() == "generated"
    assert not (root / "notes" / "todo.md").exists()
    assert not (root / "generated.txt").exists()
    assert not (root / "exec" / "notes" / "todo.md").exists()
    assert not (root / "exec" / "generated.txt").exists()
    assert not (root / "objects" / "notes" / "todo.md").exists()
    assert not (root / "objects" / "generated.txt").exists()


def test_workspace_command_timeout_returns_structured_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "python3 -c 'import time; time.sleep(2)'", "timeout_seconds": 1},
    )

    assert response.status_code == 408
    assert response.json()["error"]["code"] == "command_timeout"


def test_workspace_command_deletes_existing_logical_file(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)
    client.put(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
        content=b"before",
    )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "rm notes/todo.md"},
    )

    assert response.status_code == 200
    assert response.json()["changed_paths"] == ["/notes/todo.md"]
    missing = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/notes/todo.md"},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "path_not_found"


def test_workspace_command_invalid_persisted_path_is_rejected_and_not_materialized(tmp_path: Path):
    client, headers, root, app = make_client_with_app(tmp_path)
    object_id = str(uuid4())
    storage_key = f"{object_id}.blob"
    agent_object_root = root / "objects" / safe_agent_dir("agent-a")
    agent_object_root.mkdir(parents=True)
    (agent_object_root / storage_key).write_bytes(b"escape")
    storage = app.state.storage
    with storage.transaction() as connection:
        connection.execute(
            """
            INSERT INTO workspace_objects(agent_id, object_id, path, kind, content_type, size, content_hash, storage_key, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "agent-a",
                object_id,
                "/../escape.txt",
                "file",
                "application/octet-stream",
                6,
                hashlib.sha256(b"escape").hexdigest(),
                storage_key,
                "{}",
            ),
        )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "true"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_path"
    assert not (root / "exec" / "escape.txt").exists()
    assert not (root / "escape.txt").exists()


def test_workspace_command_timeout_kills_descendant_processes_without_syncing_partial_changes(tmp_path: Path):
    client, headers, root = make_client(tmp_path)
    command = (
        "mkdir -p out; "
        "(while true; do printf x >> out/ticks.txt; sleep 0.05; done) & "
        "python3 -c 'import time; time.sleep(2)'"
    )

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": command, "timeout_seconds": 1},
    )
    time.sleep(0.2)
    ticks_path = root / "exec" / safe_agent_dir("agent-a") / "out" / "ticks.txt"
    first_size = ticks_path.stat().st_size if ticks_path.exists() else 0
    time.sleep(0.2)
    second_size = ticks_path.stat().st_size if ticks_path.exists() else 0
    missing = client.get(
        "/v1/agents/agent-a/workspace/object",
        headers=headers,
        params={"path": "/out/ticks.txt"},
    )

    assert response.status_code == 408
    assert response.json()["error"]["code"] == "command_timeout"
    assert second_size == first_size
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "path_not_found"


def test_workspace_command_timeout_seconds_zero_is_validation_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "true", "timeout_seconds": 0},
    )

    assert response.status_code == 422


def test_workspace_command_timeout_seconds_negative_is_validation_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/agent-a/workspace/commands",
        headers=headers,
        json={"command": "true", "timeout_seconds": -1},
    )

    assert response.status_code == 422
