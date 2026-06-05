from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app
from agent_home.config import Settings


def make_client(tmp_path: Path):
    client = TestClient(create_app(Settings(database_path=tmp_path / "state.sqlite", object_root=tmp_path / "objects")))
    token = client.post("/v1/agents", json={"agent_id": "agent-a"}).json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_list_and_patch_sessions(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1", "title": "old"}).json()

    listed = client.get("/v1/agents/agent-a/sessions", headers=headers)
    patched = client.patch(
        "/v1/agents/agent-a/sessions/s1",
        headers=headers,
        json={"title": "new", "metadata": {"k": "v"}, "active_branch_id": session["active_branch_id"]},
    )

    assert listed.status_code == 200
    assert listed.json()[0]["session_id"] == "s1"
    assert patched.status_code == 200
    assert patched.json()["title"] == "new"
    assert patched.json()["metadata"] == {"k": "v"}


def test_patch_session_preserves_omitted_fields(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = client.post(
        "/v1/agents/agent-a/sessions",
        headers=headers,
        json={"session_id": "s1", "title": "old", "metadata": {"k": "v"}},
    ).json()
    active_branch_id = session["active_branch_id"]

    title_patch = client.patch("/v1/agents/agent-a/sessions/s1", headers=headers, json={"title": "new"})

    assert title_patch.status_code == 200
    assert title_patch.json()["title"] == "new"
    assert title_patch.json()["metadata"] == {"k": "v"}
    assert title_patch.json()["active_branch_id"] == active_branch_id

    metadata_patch = client.patch("/v1/agents/agent-a/sessions/s1", headers=headers, json={"metadata": {"next": True}})

    assert metadata_patch.status_code == 200
    assert metadata_patch.json()["title"] == "new"
    assert metadata_patch.json()["metadata"] == {"next": True}
    assert metadata_patch.json()["active_branch_id"] == active_branch_id


def test_patch_session_rejects_branch_from_another_session(tmp_path: Path):
    client, headers = make_client(tmp_path)
    client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"})
    other = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s2"}).json()

    patched = client.patch(
        "/v1/agents/agent-a/sessions/s1",
        headers=headers,
        json={"active_branch_id": other["active_branch_id"]},
    )

    assert patched.status_code == 404
    assert patched.json()["error"]["code"] == "branch_not_found"


def test_branch_create_and_get(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"}).json()

    created = client.post(
        "/v1/agents/agent-a/sessions/s1/branches",
        headers=headers,
        json={
            "branch_id": "b-custom",
            "parent_branch_id": session["active_branch_id"],
            "fork_checkpoint_id": "",
            "base_message_cursor": 0,
        },
    )
    loaded = client.get("/v1/agents/agent-a/branches/b-custom", headers=headers)

    assert created.status_code == 200
    assert created.json()["branch_id"] == "b-custom"
    assert created.json()["resume_head"] == ""
    assert loaded.status_code == 200
    assert loaded.json()["parent_branch_id"] == session["active_branch_id"]


def test_create_branch_under_missing_session_returns_session_not_found(tmp_path: Path):
    client, headers = make_client(tmp_path)

    created = client.post(
        "/v1/agents/agent-a/sessions/missing/branches",
        headers=headers,
        json={"branch_id": "b-custom"},
    )

    assert created.status_code == 404
    assert created.json()["error"]["code"] == "session_not_found"


def test_create_branch_rejects_parent_branch_from_another_session(tmp_path: Path):
    client, headers = make_client(tmp_path)
    client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"})
    other = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s2"}).json()

    created = client.post(
        "/v1/agents/agent-a/sessions/s1/branches",
        headers=headers,
        json={"branch_id": "b-custom", "parent_branch_id": other["active_branch_id"]},
    )

    assert created.status_code == 404
    assert created.json()["error"]["code"] == "branch_not_found"


def test_get_run_latest_run_and_checkpoint(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"}).json()
    branch_id = session["active_branch_id"]
    client.post("/v1/agents/agent-a/runs", headers=headers, json={"run_id": "r1", "session_id": "s1", "branch_id": branch_id})
    client.post(
        "/v1/agents/agent-a/messages",
        headers=headers,
        json={
            "message_id": "m1",
            "session_id": "s1",
            "branch_id": branch_id,
            "run_id": "r1",
            "role": "user",
            "content": "hello",
        },
    )
    client.post(
        "/v1/agents/agent-a/checkpoints",
        headers=headers,
        json={
            "checkpoint_id": "c1",
            "session_id": "s1",
            "branch_id": branch_id,
            "run_id": "r1",
            "kind": "user_snapshot",
            "name": "user_message_committed",
            "message_cursor": 0,
        },
    )

    run = client.get("/v1/agents/agent-a/runs/r1", headers=headers)
    latest = client.get(f"/v1/agents/agent-a/branches/{branch_id}/runs/latest", headers=headers)
    checkpoint = client.get("/v1/agents/agent-a/checkpoints/c1", headers=headers)

    assert run.status_code == 200
    assert run.json()["run_id"] == "r1"
    assert latest.status_code == 200
    assert latest.json()["run_id"] == "r1"
    assert checkpoint.status_code == 200
    assert checkpoint.json()["checkpoint_id"] == "c1"


def test_latest_run_on_empty_branch_returns_run_not_found(tmp_path: Path):
    client, headers = make_client(tmp_path)
    session = client.post("/v1/agents/agent-a/sessions", headers=headers, json={"session_id": "s1"}).json()

    latest = client.get(f"/v1/agents/agent-a/branches/{session['active_branch_id']}/runs/latest", headers=headers)

    assert latest.status_code == 404
    assert latest.json()["error"]["code"] == "run_not_found"


def test_missing_compatibility_endpoints_return_structured_errors(tmp_path: Path):
    client, headers = make_client(tmp_path)

    branch = client.get("/v1/agents/agent-a/branches/missing", headers=headers)
    run = client.get("/v1/agents/agent-a/runs/missing", headers=headers)
    checkpoint = client.get("/v1/agents/agent-a/checkpoints/missing", headers=headers)

    assert branch.status_code == 404
    assert branch.json()["error"]["code"] == "branch_not_found"
    assert run.status_code == 404
    assert run.json()["error"]["code"] == "run_not_found"
    assert checkpoint.status_code == 404
    assert checkpoint.json()["error"]["code"] == "checkpoint_not_found"
