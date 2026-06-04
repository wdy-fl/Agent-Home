from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path):
    client = TestClient(create_app(Settings(database_path=tmp_path / "state.sqlite", object_root=tmp_path / "objects")))
    response = client.post("/v1/agents", json={"agent_id": "agent-a"})
    assert response.status_code == 201
    token = response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers


def error_code(response):
    return response.json()["error"]["code"]


def create_session(client: TestClient, headers: dict[str, str], session_id: str = "s1"):
    response = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": session_id})
    assert response.status_code == 200
    return response.json()


def test_session_run_messages_and_checkpoints_round_trip(tmp_path: Path):
    client, headers = make_client(tmp_path)

    session_response = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1", "title": "demo"})
    assert session_response.status_code == 200
    session = session_response.json()
    branch_id = session["active_branch_id"]

    run_response = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch_id})
    assert run_response.status_code == 200
    run = run_response.json()

    message_response = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "role": "user", "content": "hello"})
    assert message_response.status_code == 200
    msg = message_response.json()

    checkpoint_response = client.post("/v1/agents/agent-a/checkpoints", headers=headers, json={"checkpoint_id": "c1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "kind": "user_snapshot", "name": "user_message_committed", "message_cursor": 0})
    assert checkpoint_response.status_code == 200
    checkpoint = checkpoint_response.json()

    assert session["session_id"] == "s1"
    assert branch_id
    assert run["status"] == "running"
    assert msg["sequence"] == 0
    assert checkpoint["kind"] == "user_snapshot"

    messages_response = client.get(f"/v1/agents/agent-a/branches/{branch_id}/messages", headers=headers)
    assert messages_response.status_code == 200
    messages = messages_response.json()

    checkpoints_response = client.get(f"/v1/agents/agent-a/branches/{branch_id}/checkpoints", headers=headers)
    assert checkpoints_response.status_code == 200
    checkpoints = checkpoints_response.json()

    latest_seq_response = client.get(f"/v1/agents/agent-a/branches/{branch_id}/messages/latest-sequence", headers=headers)
    assert latest_seq_response.status_code == 200
    latest_seq = latest_seq_response.json()

    assert [m["content"] for m in messages] == ["hello"]
    assert checkpoints[0]["checkpoint_id"] == "c1"
    assert latest_seq == {"sequence": 0}


def test_timeline_routes_reject_missing_and_wrong_bearer_tokens(tmp_path: Path):
    client, headers = make_client(tmp_path)

    missing = client.post("/v1/agents/agent-a/sessions", json={"session_id": "s1"})
    wrong = client.post("/v1/agents/agent-a/sessions", headers={"Authorization": "Bearer wrong-token"}, json={"session_id": "s1"})

    assert missing.status_code == 401
    assert error_code(missing) == "auth_failed"
    assert wrong.status_code == 401
    assert error_code(wrong) == "auth_failed"


def test_invalid_references_return_structured_not_found_errors(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = create_session(client, headers)
    branch_id = session["active_branch_id"]

    missing_session_run = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "missing", "branch_id": branch_id})
    missing_branch_run = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r2", "session_id": "s1", "branch_id": "missing"})
    run_response = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r3", "session_id": "s1", "branch_id": branch_id})
    assert run_response.status_code == 200

    missing_branch_message = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": "missing", "run_id": "r3", "role": "user", "content": "hello"})
    missing_run_message = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m2", "session_id": "s1", "branch_id": branch_id, "run_id": "missing", "role": "user", "content": "hello"})

    assert missing_session_run.status_code == 404
    assert error_code(missing_session_run) == "session_not_found"
    assert missing_branch_run.status_code == 404
    assert error_code(missing_branch_run) == "branch_not_found"
    assert missing_branch_message.status_code == 404
    assert error_code(missing_branch_message) == "branch_not_found"
    assert missing_run_message.status_code == 404
    assert error_code(missing_run_message) == "run_not_found"


def test_duplicate_timeline_ids_return_conflicts(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = create_session(client, headers)
    branch_id = session["active_branch_id"]
    run = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch_id})
    assert run.status_code == 200
    message = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "role": "user", "content": "hello"})
    assert message.status_code == 200
    checkpoint = client.post("/v1/agents/agent-a/checkpoints", headers=headers, json={"checkpoint_id": "c1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "kind": "user_snapshot", "name": "user_message_committed", "message_cursor": 0})
    assert checkpoint.status_code == 200

    duplicate_session = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"})
    duplicate_run = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch_id})
    duplicate_message = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "role": "user", "content": "hello"})
    duplicate_checkpoint = client.post("/v1/agents/agent-a/checkpoints", headers=headers, json={"checkpoint_id": "c1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "kind": "user_snapshot", "name": "user_message_committed", "message_cursor": 0})

    assert duplicate_session.status_code == 409
    assert error_code(duplicate_session) == "session_exists"
    assert duplicate_run.status_code == 409
    assert error_code(duplicate_run) == "run_exists"
    assert duplicate_message.status_code == 409
    assert error_code(duplicate_message) == "message_exists"
    assert duplicate_checkpoint.status_code == 409
    assert error_code(duplicate_checkpoint) == "checkpoint_exists"


def test_multiple_messages_sequence_and_completed_run_status(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = create_session(client, headers)
    branch_id = session["active_branch_id"]
    run_response = client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch_id})
    assert run_response.status_code == 200

    first = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "role": "user", "content": "first"})
    second = client.post("/v1/agents/agent-a/messages", headers=headers, json={"message_id": "m2", "session_id": "s1", "branch_id": branch_id, "run_id": "r1", "role": "assistant", "content": "second"})
    completed = client.patch("/v1/agents/agent-a/runs/r1/status", headers=headers, json={"status": "completed"})

    assert first.status_code == 200
    assert first.json()["sequence"] == 0
    assert second.status_code == 200
    assert second.json()["sequence"] == 1
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_missing_session_and_run_status_return_structured_not_found_errors(tmp_path: Path):
    client, headers = make_client(tmp_path)

    session = client.get("/v1/agents/agent-a/sessions/missing", headers=headers)
    run = client.patch("/v1/agents/agent-a/runs/missing/status", headers=headers, json={"status": "completed"})

    assert session.status_code == 404
    assert error_code(session) == "session_not_found"
    assert run.status_code == 404
    assert error_code(run) == "run_not_found"


def test_latest_sequence_on_empty_branch_returns_negative_one(tmp_path: Path):
    client, headers = make_client(tmp_path)

    session = create_session(client, headers)
    branch_id = session["active_branch_id"]

    latest_seq_response = client.get(f"/v1/agents/agent-a/branches/{branch_id}/messages/latest-sequence", headers=headers)
    assert latest_seq_response.status_code == 200
    latest_seq = latest_seq_response.json()

    assert latest_seq == {"sequence": -1}
