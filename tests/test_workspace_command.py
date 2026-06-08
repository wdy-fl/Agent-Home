import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client_with_app(tmp_path: Path, agent_id: str = "123") -> tuple[TestClient, dict[str, str], Path, FastAPI]:
    workspace_root = tmp_path / "workspace"
    app = create_app(Settings(database_path=tmp_path / "state.sqlite", workspace_root=workspace_root))
    client = TestClient(app)
    token = client.post("/v1/agents", json={"agent_id": agent_id}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}, workspace_root, app


def make_client(tmp_path: Path, agent_id: str = "123") -> tuple[TestClient, dict[str, str], Path]:
    client, headers, workspace_root, _ = make_client_with_app(tmp_path, agent_id)
    return client, headers, workspace_root


def test_workspace_command_writes_file_in_agent_workspace(tmp_path: Path):
    client, headers, workspace_root = make_client(tmp_path)

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "printf hello > hello.txt"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"exit_code": 0, "stdout": "", "stderr": ""}
    assert set(body) == {"exit_code", "stdout", "stderr"}
    assert (workspace_root / "123" / "hello.txt").read_text(encoding="utf-8") == "hello"


def test_workspace_command_reads_file_from_previous_command(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)
    first = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "mkdir -p notes && printf before > notes/todo.md"},
    )

    second = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "cat notes/todo.md"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["exit_code"] == 0
    assert second.json()["stdout"] == "before"


def test_workspace_command_nonzero_exit_keeps_file_changes(tmp_path: Path):
    client, headers, workspace_root = make_client(tmp_path)

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "mkdir -p reports; printf failed > reports/status.txt; exit 7", "timeout_seconds": 20},
    )

    assert response.status_code == 200
    assert response.json() == {"exit_code": 7, "stdout": "", "stderr": ""}
    assert (workspace_root / "123" / "reports" / "status.txt").read_text(encoding="utf-8") == "failed"


def test_workspace_command_uses_separate_directories_for_different_agents(tmp_path: Path):
    client_123, headers_123, workspace_root = make_client(tmp_path, "123")
    token_456 = client_123.post("/v1/agents", json={"agent_id": "456"}).json()["token"]
    headers_456 = {"Authorization": f"Bearer {token_456}"}

    write_123 = client_123.post(
        "/v1/agents/123/workspace/commands",
        headers=headers_123,
        json={"command": "printf secret > note.txt"},
    )
    check_456 = client_123.post(
        "/v1/agents/456/workspace/commands",
        headers=headers_456,
        json={"command": "test ! -e note.txt"},
    )

    assert write_123.status_code == 200
    assert check_456.status_code == 200
    assert check_456.json()["exit_code"] == 0
    assert (workspace_root / "123" / "note.txt").read_text(encoding="utf-8") == "secret"
    assert not (workspace_root / "456" / "note.txt").exists()


def test_workspace_command_rejects_missing_and_wrong_bearer_tokens(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    missing = client.post("/v1/agents/123/workspace/commands", json={"command": "true"})
    wrong = client.post(
        "/v1/agents/123/workspace/commands",
        headers={"Authorization": "Bearer wrong-token"},
        json={"command": "true"},
    )
    ok = client.post("/v1/agents/123/workspace/commands", headers=headers, json={"command": "true"})

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "auth_failed"
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "auth_failed"
    assert ok.status_code == 200


def test_workspace_command_timeout_returns_structured_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "python3 -c 'import time; time.sleep(2)'", "timeout_seconds": 1},
    )

    assert response.status_code == 408
    assert response.json()["error"]["code"] == "command_timeout"


def test_workspace_command_timeout_kills_descendant_processes_but_keeps_partial_files(tmp_path: Path):
    client, headers, workspace_root = make_client(tmp_path)
    command = (
        "mkdir -p out; "
        "(while true; do printf x >> out/ticks.txt; sleep 0.05; done) & "
        "python3 -c 'import time; time.sleep(2)'"
    )

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": command, "timeout_seconds": 1},
    )
    time.sleep(0.2)
    ticks_path = workspace_root / "123" / "out" / "ticks.txt"
    first_size = ticks_path.stat().st_size if ticks_path.exists() else 0
    time.sleep(0.2)
    second_size = ticks_path.stat().st_size if ticks_path.exists() else 0

    assert response.status_code == 408
    assert response.json()["error"]["code"] == "command_timeout"
    assert first_size > 0
    assert second_size == first_size


def test_workspace_command_timeout_seconds_zero_is_validation_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "true", "timeout_seconds": 0},
    )

    assert response.status_code == 422


def test_workspace_command_timeout_seconds_negative_is_validation_error(tmp_path: Path):
    client, headers, _ = make_client(tmp_path)

    response = client.post(
        "/v1/agents/123/workspace/commands",
        headers=headers,
        json={"command": "true", "timeout_seconds": -1},
    )

    assert response.status_code == 422
