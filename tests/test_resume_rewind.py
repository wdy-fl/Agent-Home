from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path):
    client = TestClient(create_app(Settings(database_path=tmp_path / "state.sqlite", workspace_root=tmp_path / "workspace")))
    token = client.post("/v1/agents", json={"agent_id": "123"}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def seed_two_turns(client: TestClient, headers: dict[str, str]):
    session_response = client.post("/v1/agents/123/sessions", headers=headers, json={"session_id": "s1"})
    assert session_response.status_code == 200
    session = session_response.json()
    branch = session["active_branch_id"]
    assert client.post("/v1/agents/123/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch}).status_code == 200
    assert client.post("/v1/agents/123/messages", headers=headers, json={"message_id": "m1", "session_id": "s1", "branch_id": branch, "run_id": "r1", "role": "user", "content": "first"}).status_code == 200
    assert client.post("/v1/agents/123/checkpoints", headers=headers, json={"checkpoint_id": "c1", "session_id": "s1", "branch_id": branch, "run_id": "r1", "kind": "user_snapshot", "name": "user_message_committed", "message_cursor": 0}).status_code == 200
    assert client.post("/v1/agents/123/messages", headers=headers, json={"message_id": "m2", "session_id": "s1", "branch_id": branch, "run_id": "r1", "role": "assistant", "content": "answer first"}).status_code == 200
    assert client.post("/v1/agents/123/checkpoints", headers=headers, json={"checkpoint_id": "done1", "session_id": "s1", "branch_id": branch, "run_id": "r1", "kind": "runtime", "name": "run_completed", "message_cursor": 1}).status_code == 200
    assert client.patch(f"/v1/agents/123/branches/{branch}", headers=headers, json={"resume_head": "done1"}).status_code == 200
    assert client.patch("/v1/agents/123/runs/r1/status", headers=headers, json={"status": "completed"}).status_code == 200
    return branch


def error_code(response):
    return response.json()["error"]["code"]


def test_resume_returns_messages_up_to_resume_head(tmp_path: Path):
    client, headers = make_client(tmp_path)
    seed_two_turns(client, headers)

    response = client.post("/v1/agents/123/sessions/s1/resume", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [m["content"] for m in body["messages"]] == ["first", "answer first"]
    assert body["interrupted_info"] is None


def test_rewind_creates_new_branch_without_copying_messages(tmp_path: Path):
    client, headers = make_client(tmp_path)
    old_branch = seed_two_turns(client, headers)

    response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "c1"})

    assert response.status_code == 200
    body = response.json()
    assert body["new_branch_id"] != old_branch
    assert [m["content"] for m in body["messages"]] == ["first"]
    own_messages = client.get(f"/v1/agents/123/branches/{body['new_branch_id']}/messages", headers=headers).json()
    assert own_messages == []


def test_rewind_updates_active_branch_and_resume_uses_new_branch(tmp_path: Path):
    client, headers = make_client(tmp_path)
    old_branch = seed_two_turns(client, headers)

    rewind_response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "c1"})

    assert rewind_response.status_code == 200
    new_branch = rewind_response.json()["new_branch_id"]
    session_response = client.get("/v1/agents/123/sessions/s1", headers=headers)
    resume_response = client.post("/v1/agents/123/sessions/s1/resume", headers=headers)
    assert session_response.json()["active_branch_id"] == new_branch
    assert resume_response.json()["branch_id"] == new_branch
    assert resume_response.json()["branch_id"] != old_branch
    assert [m["content"] for m in resume_response.json()["messages"]] == ["first"]


def test_rewind_rejects_runtime_checkpoint(tmp_path: Path):
    client, headers = make_client(tmp_path)
    seed_two_turns(client, headers)

    response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "done1"})

    assert response.status_code == 400
    assert error_code(response) == "invalid_checkpoint_kind"


def test_resume_reports_interrupted_info_only_for_latest_run_on_active_branch(tmp_path: Path):
    client, headers = make_client(tmp_path)
    branch = seed_two_turns(client, headers)
    client.post("/v1/agents/123/runs", headers=headers, json={"run_id": "r2", "session_id": "s1", "branch_id": branch})
    client.patch("/v1/agents/123/runs/r2/status", headers=headers, json={"status": "interrupted"})

    interrupted_resume = client.post("/v1/agents/123/sessions/s1/resume", headers=headers)

    assert interrupted_resume.status_code == 200
    assert interrupted_resume.json()["interrupted_info"] == "interrupted"

    client.post("/v1/agents/123/runs", headers=headers, json={"run_id": "r3", "session_id": "s1", "branch_id": branch})
    client.patch("/v1/agents/123/runs/r3/status", headers=headers, json={"status": "completed"})

    completed_resume = client.post("/v1/agents/123/sessions/s1/resume", headers=headers)

    assert completed_resume.status_code == 200
    assert completed_resume.json()["interrupted_info"] is None


def test_resume_head_excludes_messages_after_checkpoint_cursor(tmp_path: Path):
    client, headers = make_client(tmp_path)
    branch = seed_two_turns(client, headers)
    client.post("/v1/agents/123/messages", headers=headers, json={"message_id": "m3", "session_id": "s1", "branch_id": branch, "run_id": "r1", "role": "user", "content": "after done"})

    response = client.post("/v1/agents/123/sessions/s1/resume", headers=headers)

    assert response.status_code == 200
    assert [m["content"] for m in response.json()["messages"]] == ["first", "answer first"]


def test_update_resume_head_rejects_checkpoint_from_another_branch(tmp_path: Path):
    client, headers = make_client(tmp_path)
    original_branch = seed_two_turns(client, headers)
    rewind_response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "c1"})
    new_branch = rewind_response.json()["new_branch_id"]

    response = client.patch(f"/v1/agents/123/branches/{new_branch}", headers=headers, json={"resume_head": "done1"})

    assert original_branch != new_branch
    assert response.status_code == 404
    assert error_code(response) == "checkpoint_not_found"


def test_nested_rewind_visible_history_includes_ancestor_and_parent_messages_without_copying(tmp_path: Path):
    client, headers = make_client(tmp_path)
    branch_a = seed_two_turns(client, headers)
    rewind_b_response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "c1"})
    branch_b = rewind_b_response.json()["new_branch_id"]
    client.post("/v1/agents/123/runs", headers=headers, json={"run_id": "r2", "session_id": "s1", "branch_id": branch_b})
    client.post("/v1/agents/123/messages", headers=headers, json={"message_id": "m3", "session_id": "s1", "branch_id": branch_b, "run_id": "r2", "role": "assistant", "content": "second path"})
    client.post("/v1/agents/123/checkpoints", headers=headers, json={"checkpoint_id": "c3", "session_id": "s1", "branch_id": branch_b, "run_id": "r2", "kind": "user_snapshot", "name": "branch_b_message_committed", "message_cursor": 0})

    rewind_c_response = client.post("/v1/agents/123/sessions/s1/rewind", headers=headers, json={"checkpoint_id": "c3"})

    assert rewind_c_response.status_code == 200
    branch_c = rewind_c_response.json()["new_branch_id"]
    assert branch_c not in {branch_a, branch_b}
    assert [m["content"] for m in rewind_c_response.json()["messages"]] == ["first", "second path"]
    own_messages = client.get(f"/v1/agents/123/branches/{branch_c}/messages", headers=headers).json()
    assert own_messages == []


def test_create_checkpoint_rejects_message_cursor_beyond_latest_sequence(tmp_path: Path):
    client, headers = make_client(tmp_path)
    branch = seed_two_turns(client, headers)

    response = client.post("/v1/agents/123/checkpoints", headers=headers, json={"checkpoint_id": "bad-cursor", "session_id": "s1", "branch_id": branch, "run_id": "r1", "kind": "user_snapshot", "name": "bad_cursor", "message_cursor": 2})

    assert response.status_code == 400
    assert error_code(response) == "invalid_message_cursor"
