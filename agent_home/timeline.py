from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, Request

from agent_home.errors import raise_error
from agent_home.models import (
    BranchResponse,
    CheckpointKind,
    CheckpointResponse,
    CreateBranchRequest,
    CreateCheckpointRequest,
    CreateMessageRequest,
    CreateRunRequest,
    CreateSessionRequest,
    MessageResponse,
    ResumeResponse,
    RewindRequest,
    RewindResponse,
    RunResponse,
    RunStatus,
    SessionResponse,
    UpdateBranchRequest,
    UpdateRunStatusRequest,
    UpdateSessionRequest,
)

router = APIRouter()


def storage(request: Request):
    return request.app.state.storage


def session_response(row: sqlite3.Row) -> SessionResponse:
    return SessionResponse(
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        title=row["title"],
        metadata=json.loads(row["metadata"]),
        active_branch_id=row["active_branch_id"],
    )


def run_response(row: sqlite3.Row) -> RunResponse:
    return RunResponse(
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        branch_id=row["branch_id"],
        status=RunStatus(row["status"]),
    )


def branch_response(row: sqlite3.Row) -> BranchResponse:
    return BranchResponse(
        branch_id=row["branch_id"],
        session_id=row["session_id"],
        parent_branch_id=row["parent_branch_id"],
        fork_checkpoint_id=row["fork_checkpoint_id"],
        base_message_cursor=row["base_message_cursor"],
        resume_head=row["resume_head"],
    )


row_to_branch = branch_response


def message_response(row: sqlite3.Row) -> MessageResponse:
    return MessageResponse(
        message_id=row["message_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        branch_id=row["branch_id"],
        run_id=row["run_id"],
        role=row["role"],
        content=row["content"],
        metadata=json.loads(row["metadata"]),
        tool_call_id=row["tool_call_id"],
        tool_calls=json.loads(row["tool_calls"]),
        sequence=row["sequence"],
    )


def checkpoint_response(row: sqlite3.Row) -> CheckpointResponse:
    return CheckpointResponse(
        checkpoint_id=row["checkpoint_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        branch_id=row["branch_id"],
        run_id=row["run_id"],
        kind=row["kind"],
        name=row["name"],
        message_cursor=row["message_cursor"],
        metadata=json.loads(row["metadata"]),
    )


def require_session(connection: sqlite3.Connection, agent_id: str, session_id: str) -> sqlite3.Row:
    row_result = connection.execute(
        """
        SELECT session_id, agent_id, title, metadata, active_branch_id
        FROM sessions
        WHERE agent_id = ? AND session_id = ?
        """,
        (agent_id, session_id),
    ).fetchone()
    if row_result is None:
        raise_error("session_not_found", f"session {session_id} not found")
    assert row_result is not None
    return row_result


def require_branch(connection: sqlite3.Connection, agent_id: str, branch_id: str) -> sqlite3.Row:
    row_result = connection.execute(
        """
        SELECT branch_id, session_id, parent_branch_id, fork_checkpoint_id, base_message_cursor, resume_head
        FROM branches
        WHERE agent_id = ? AND branch_id = ?
        """,
        (agent_id, branch_id),
    ).fetchone()
    if row_result is None:
        raise_error("branch_not_found", f"branch {branch_id} not found")
    assert row_result is not None
    return row_result


def require_run(connection: sqlite3.Connection, agent_id: str, run_id: str) -> sqlite3.Row:
    row_result = connection.execute(
        """
        SELECT run_id, agent_id, session_id, branch_id, status
        FROM runs
        WHERE agent_id = ? AND run_id = ?
        """,
        (agent_id, run_id),
    ).fetchone()
    if row_result is None:
        raise_error("run_not_found", f"run {run_id} not found")
    assert row_result is not None
    return row_result


def require_checkpoint(connection: sqlite3.Connection, agent_id: str, checkpoint_id: str) -> sqlite3.Row:
    row_result = connection.execute(
        """
        SELECT checkpoint_id, agent_id, session_id, branch_id, run_id, kind, name, message_cursor, metadata
        FROM checkpoints
        WHERE agent_id = ? AND checkpoint_id = ?
        """,
        (agent_id, checkpoint_id),
    ).fetchone()
    if row_result is None:
        raise_error("checkpoint_not_found", f"checkpoint {checkpoint_id} not found")
    assert row_result is not None
    return row_result


def validate_session_branch(connection: sqlite3.Connection, agent_id: str, session_id: str, branch_id: str) -> sqlite3.Row:
    require_session(connection, agent_id, session_id)
    branch = require_branch(connection, agent_id, branch_id)
    if branch["session_id"] != session_id:
        raise_error("branch_not_found", f"branch {branch_id} not found for session {session_id}")
    return branch


def validate_session_branch_run(connection: sqlite3.Connection, agent_id: str, session_id: str, branch_id: str, run_id: str | None) -> None:
    validate_session_branch(connection, agent_id, session_id, branch_id)
    if run_id is None:
        return
    run = require_run(connection, agent_id, run_id)
    if run["session_id"] != session_id or run["branch_id"] != branch_id:
        raise_error("run_not_found", f"run {run_id} not found for session {session_id} and branch {branch_id}")


def collect_visible_messages(agent_id: str, branch_row: sqlite3.Row, request: Request, up_to_cursor: int | None = None) -> list[MessageResponse]:
    connection = storage(request)._conn
    branch_chain: list[sqlite3.Row] = []
    current = branch_row
    while current is not None:
        branch_chain.append(current)
        if not current["parent_branch_id"]:
            break
        current = require_branch(connection, agent_id, current["parent_branch_id"])

    rows: list[sqlite3.Row] = []
    for index, branch in enumerate(reversed(branch_chain)):
        child_index = len(branch_chain) - index - 2
        limit = branch_chain[child_index]["base_message_cursor"] if child_index >= 0 else up_to_cursor
        clauses = ["agent_id = ?", "branch_id = ?"]
        values: list[object] = [agent_id, branch["branch_id"]]
        if limit is not None:
            clauses.append("sequence <= ?")
            values.append(limit)
        branch_rows = connection.execute(
            f"""
            SELECT message_id, agent_id, session_id, branch_id, run_id, role, content, metadata, tool_call_id, tool_calls, sequence
            FROM messages
            WHERE {' AND '.join(clauses)}
            ORDER BY sequence ASC
            """,
            values,
        ).fetchall()
        rows.extend(branch_rows)
    return [message_response(row) for row in rows]


@router.post("/v1/agents/{agent_id}/sessions", response_model=SessionResponse)
def create_session(agent_id: str, request: CreateSessionRequest, http_request: Request) -> SessionResponse:
    branch_id = str(uuid4())
    try:
        with storage(http_request).transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions(session_id, agent_id, title, metadata, active_branch_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request.session_id, agent_id, request.title, json.dumps(request.metadata), branch_id),
            )
            connection.execute(
                """
                INSERT INTO branches(branch_id, session_id, agent_id, parent_branch_id, fork_checkpoint_id, base_message_cursor, resume_head)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (branch_id, request.session_id, agent_id, "", "", 0, ""),
            )
    except sqlite3.IntegrityError:
        raise_error("session_exists", f"session {request.session_id} already exists")
    row = storage(http_request)._conn.execute(
        """
        SELECT session_id, agent_id, title, metadata, active_branch_id
        FROM sessions
        WHERE agent_id = ? AND session_id = ?
        """,
        (agent_id, request.session_id),
    ).fetchone()
    return session_response(row)


@router.get("/v1/agents/{agent_id}/sessions", response_model=list[SessionResponse])
def list_sessions(agent_id: str, http_request: Request) -> list[SessionResponse]:
    rows = storage(http_request)._conn.execute(
        """
        SELECT session_id, agent_id, title, metadata, active_branch_id
        FROM sessions
        WHERE agent_id = ?
        ORDER BY updated_at DESC
        """,
        (agent_id,),
    ).fetchall()
    return [session_response(row) for row in rows]


@router.get("/v1/agents/{agent_id}/sessions/{session_id}", response_model=SessionResponse)
def get_session(agent_id: str, session_id: str, http_request: Request) -> SessionResponse:
    row = require_session(storage(http_request)._conn, agent_id, session_id)
    return session_response(row)


@router.patch("/v1/agents/{agent_id}/sessions/{session_id}", response_model=SessionResponse)
def update_session(agent_id: str, session_id: str, request: UpdateSessionRequest, http_request: Request) -> SessionResponse:
    with storage(http_request).transaction() as connection:
        require_session(connection, agent_id, session_id)
        if request.active_branch_id is not None:
            validate_session_branch(connection, agent_id, session_id, request.active_branch_id)
        if request.title is not None:
            connection.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND session_id = ?
                """,
                (request.title, agent_id, session_id),
            )
        if request.metadata is not None:
            connection.execute(
                """
                UPDATE sessions
                SET metadata = ?, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND session_id = ?
                """,
                (json.dumps(request.metadata), agent_id, session_id),
            )
        if request.active_branch_id is not None:
            connection.execute(
                """
                UPDATE sessions
                SET active_branch_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND session_id = ?
                """,
                (request.active_branch_id, agent_id, session_id),
            )
    row = require_session(storage(http_request)._conn, agent_id, session_id)
    return session_response(row)


@router.post("/v1/agents/{agent_id}/sessions/{session_id}/branches", response_model=BranchResponse)
def create_branch(agent_id: str, session_id: str, request: CreateBranchRequest, http_request: Request) -> BranchResponse:
    branch_id = request.branch_id or str(uuid4())
    try:
        with storage(http_request).transaction() as connection:
            require_session(connection, agent_id, session_id)
            if request.parent_branch_id:
                validate_session_branch(connection, agent_id, session_id, request.parent_branch_id)
            connection.execute(
                """
                INSERT INTO branches(branch_id, session_id, agent_id, parent_branch_id, fork_checkpoint_id, base_message_cursor, resume_head)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (branch_id, session_id, agent_id, request.parent_branch_id, request.fork_checkpoint_id, request.base_message_cursor, ""),
            )
    except sqlite3.IntegrityError:
        raise_error("branch_exists", f"branch {branch_id} already exists")
    row = require_branch(storage(http_request)._conn, agent_id, branch_id)
    return branch_response(row)


@router.post("/v1/agents/{agent_id}/sessions/{session_id}/resume", response_model=ResumeResponse)
def resume_session(agent_id: str, session_id: str, http_request: Request) -> ResumeResponse:
    connection = storage(http_request)._conn
    session = require_session(connection, agent_id, session_id)
    branch = require_branch(connection, agent_id, session["active_branch_id"])
    up_to_cursor = None
    if branch["resume_head"]:
        checkpoint = require_checkpoint(connection, agent_id, branch["resume_head"])
        if checkpoint["branch_id"] != branch["branch_id"]:
            raise_error("checkpoint_not_found", f"checkpoint {branch['resume_head']} not found for branch {branch['branch_id']}")
        up_to_cursor = checkpoint["message_cursor"]
    latest_run = connection.execute(
        """
        SELECT run_id, agent_id, session_id, branch_id, status
        FROM runs
        WHERE agent_id = ? AND branch_id = ?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (agent_id, branch["branch_id"]),
    ).fetchone()
    interrupted_info = "interrupted" if latest_run is not None and latest_run["status"] == RunStatus.interrupted.value else None
    return ResumeResponse(
        session_id=session_id,
        branch_id=branch["branch_id"],
        messages=collect_visible_messages(agent_id, branch, http_request, up_to_cursor),
        interrupted_info=interrupted_info,
    )


@router.post("/v1/agents/{agent_id}/sessions/{session_id}/rewind", response_model=RewindResponse)
def rewind_session(agent_id: str, session_id: str, request: RewindRequest, http_request: Request) -> RewindResponse:
    new_branch_id = str(uuid4())
    with storage(http_request).transaction() as connection:
        require_session(connection, agent_id, session_id)
        checkpoint = require_checkpoint(connection, agent_id, request.checkpoint_id)
        if checkpoint["session_id"] != session_id:
            raise_error("checkpoint_not_found", f"checkpoint {request.checkpoint_id} not found for session {session_id}")
        if checkpoint["kind"] != CheckpointKind.user_snapshot.value:
            raise_error("invalid_checkpoint_kind", f"checkpoint {request.checkpoint_id} is not a user snapshot")
        parent_branch = require_branch(connection, agent_id, checkpoint["branch_id"])
        connection.execute(
            """
            INSERT INTO branches(branch_id, session_id, agent_id, parent_branch_id, fork_checkpoint_id, base_message_cursor, resume_head)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_branch_id, session_id, agent_id, parent_branch["branch_id"], checkpoint["checkpoint_id"], checkpoint["message_cursor"], ""),
        )
        connection.execute(
            """
            UPDATE sessions
            SET active_branch_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ? AND session_id = ?
            """,
            (new_branch_id, agent_id, session_id),
        )
    branch = require_branch(storage(http_request)._conn, agent_id, new_branch_id)
    return RewindResponse(new_branch_id=new_branch_id, messages=collect_visible_messages(agent_id, branch, http_request))


@router.get("/v1/agents/{agent_id}/branches/{branch_id}", response_model=BranchResponse)
def get_branch(agent_id: str, branch_id: str, http_request: Request) -> BranchResponse:
    row = require_branch(storage(http_request)._conn, agent_id, branch_id)
    return branch_response(row)


@router.post("/v1/agents/{agent_id}/runs", response_model=RunResponse)
def create_run(agent_id: str, request: CreateRunRequest, http_request: Request) -> RunResponse:
    try:
        with storage(http_request).transaction() as connection:
            validate_session_branch(connection, agent_id, request.session_id, request.branch_id)
            connection.execute(
                """
                INSERT INTO runs(run_id, agent_id, session_id, branch_id, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request.run_id, agent_id, request.session_id, request.branch_id, RunStatus.running.value),
            )
    except sqlite3.IntegrityError:
        raise_error("run_exists", f"run {request.run_id} already exists")
    row = storage(http_request)._conn.execute(
        """
        SELECT run_id, agent_id, session_id, branch_id, status
        FROM runs
        WHERE agent_id = ? AND run_id = ?
        """,
        (agent_id, request.run_id),
    ).fetchone()
    return run_response(row)


@router.get("/v1/agents/{agent_id}/runs/{run_id}", response_model=RunResponse)
def get_run(agent_id: str, run_id: str, http_request: Request) -> RunResponse:
    row = require_run(storage(http_request)._conn, agent_id, run_id)
    return run_response(row)


@router.get("/v1/agents/{agent_id}/branches/{branch_id}/runs/latest", response_model=RunResponse)
def get_latest_branch_run(agent_id: str, branch_id: str, http_request: Request) -> RunResponse:
    require_branch(storage(http_request)._conn, agent_id, branch_id)
    row_result = storage(http_request)._conn.execute(
        """
        SELECT run_id, agent_id, session_id, branch_id, status
        FROM runs
        WHERE agent_id = ? AND branch_id = ?
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (agent_id, branch_id),
    ).fetchone()
    if row_result is None:
        raise_error("run_not_found", f"run not found for branch {branch_id}")
    assert row_result is not None
    return run_response(row_result)


@router.patch("/v1/agents/{agent_id}/runs/{run_id}/status", response_model=RunResponse)
def update_run_status(agent_id: str, run_id: str, request: UpdateRunStatusRequest, http_request: Request) -> RunResponse:
    with storage(http_request).transaction() as connection:
        require_run(connection, agent_id, run_id)
        connection.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ? AND run_id = ?
            """,
            (request.status.value, agent_id, run_id),
        )
    row = storage(http_request)._conn.execute(
        """
        SELECT run_id, agent_id, session_id, branch_id, status
        FROM runs
        WHERE agent_id = ? AND run_id = ?
        """,
        (agent_id, run_id),
    ).fetchone()
    return run_response(row)


@router.patch("/v1/agents/{agent_id}/branches/{branch_id}", response_model=BranchResponse)
def update_branch(agent_id: str, branch_id: str, request: UpdateBranchRequest, http_request: Request) -> BranchResponse:
    with storage(http_request).transaction() as connection:
        require_branch(connection, agent_id, branch_id)
        if request.resume_head is not None:
            if request.resume_head:
                checkpoint = require_checkpoint(connection, agent_id, request.resume_head)
                if checkpoint["branch_id"] != branch_id:
                    raise_error("checkpoint_not_found", f"checkpoint {request.resume_head} not found for branch {branch_id}")
            connection.execute(
                """
                UPDATE branches
                SET resume_head = ?
                WHERE agent_id = ? AND branch_id = ?
                """,
                (request.resume_head, agent_id, branch_id),
            )
    row = require_branch(storage(http_request)._conn, agent_id, branch_id)
    return branch_response(row)


@router.post("/v1/agents/{agent_id}/messages", response_model=MessageResponse)
def create_message(agent_id: str, request: CreateMessageRequest, http_request: Request) -> MessageResponse:
    try:
        with storage(http_request).transaction() as connection:
            validate_session_branch_run(connection, agent_id, request.session_id, request.branch_id, request.run_id)
            current = connection.execute(
                """
                SELECT MAX(sequence) AS latest
                FROM messages
                WHERE agent_id = ? AND branch_id = ?
                """,
                (agent_id, request.branch_id),
            ).fetchone()
            sequence = 0 if current["latest"] is None else current["latest"] + 1
            connection.execute(
                """
                INSERT INTO messages(message_id, agent_id, session_id, branch_id, run_id, role, content, metadata, tool_call_id, tool_calls, sequence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.message_id,
                    agent_id,
                    request.session_id,
                    request.branch_id,
                    request.run_id,
                    request.role,
                    request.content,
                    json.dumps(request.metadata),
                    request.tool_call_id,
                    json.dumps(request.tool_calls),
                    sequence,
                ),
            )
    except sqlite3.IntegrityError:
        raise_error("message_exists", f"message {request.message_id} already exists")
    row = storage(http_request)._conn.execute(
        """
        SELECT message_id, agent_id, session_id, branch_id, run_id, role, content, metadata, tool_call_id, tool_calls, sequence
        FROM messages
        WHERE agent_id = ? AND message_id = ?
        """,
        (agent_id, request.message_id),
    ).fetchone()
    return message_response(row)


@router.get("/v1/agents/{agent_id}/branches/{branch_id}/messages", response_model=list[MessageResponse])
def list_messages(agent_id: str, branch_id: str, http_request: Request, start: int | None = None, end: int | None = None) -> list[MessageResponse]:
    require_branch(storage(http_request)._conn, agent_id, branch_id)
    clauses = ["agent_id = ?", "branch_id = ?"]
    values: list[object] = [agent_id, branch_id]
    if start is not None:
        clauses.append("sequence >= ?")
        values.append(start)
    if end is not None:
        clauses.append("sequence <= ?")
        values.append(end)
    rows = storage(http_request)._conn.execute(
        f"""
        SELECT message_id, agent_id, session_id, branch_id, run_id, role, content, metadata, tool_call_id, tool_calls, sequence
        FROM messages
        WHERE {' AND '.join(clauses)}
        ORDER BY sequence ASC
        """,
        values,
    ).fetchall()
    return [message_response(row) for row in rows]


@router.get("/v1/agents/{agent_id}/branches/{branch_id}/messages/latest-sequence")
def latest_message_sequence(agent_id: str, branch_id: str, http_request: Request) -> dict[str, int]:
    require_branch(storage(http_request)._conn, agent_id, branch_id)
    row = storage(http_request)._conn.execute(
        """
        SELECT MAX(sequence) AS latest
        FROM messages
        WHERE agent_id = ? AND branch_id = ?
        """,
        (agent_id, branch_id),
    ).fetchone()
    return {"sequence": -1 if row["latest"] is None else row["latest"]}


@router.post("/v1/agents/{agent_id}/checkpoints", response_model=CheckpointResponse)
def create_checkpoint(agent_id: str, request: CreateCheckpointRequest, http_request: Request) -> CheckpointResponse:
    try:
        with storage(http_request).transaction() as connection:
            validate_session_branch_run(connection, agent_id, request.session_id, request.branch_id, request.run_id)
            latest = connection.execute(
                """
                SELECT MAX(sequence) AS latest
                FROM messages
                WHERE agent_id = ? AND branch_id = ?
                """,
                (agent_id, request.branch_id),
            ).fetchone()["latest"]
            if request.message_cursor < 0 or latest is None or request.message_cursor > latest:
                raise_error("invalid_message_cursor", f"message cursor {request.message_cursor} is invalid for branch {request.branch_id}")
            connection.execute(
                """
                INSERT INTO checkpoints(checkpoint_id, agent_id, session_id, branch_id, run_id, kind, name, message_cursor, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.checkpoint_id,
                    agent_id,
                    request.session_id,
                    request.branch_id,
                    request.run_id,
                    request.kind.value,
                    request.name,
                    request.message_cursor,
                    json.dumps(request.metadata),
                ),
            )
    except sqlite3.IntegrityError:
        raise_error("checkpoint_exists", f"checkpoint {request.checkpoint_id} already exists")
    row = storage(http_request)._conn.execute(
        """
        SELECT checkpoint_id, agent_id, session_id, branch_id, run_id, kind, name, message_cursor, metadata
        FROM checkpoints
        WHERE agent_id = ? AND checkpoint_id = ?
        """,
        (agent_id, request.checkpoint_id),
    ).fetchone()
    return checkpoint_response(row)


@router.get("/v1/agents/{agent_id}/checkpoints/{checkpoint_id}", response_model=CheckpointResponse)
def get_checkpoint(agent_id: str, checkpoint_id: str, http_request: Request) -> CheckpointResponse:
    row = require_checkpoint(storage(http_request)._conn, agent_id, checkpoint_id)
    return checkpoint_response(row)


@router.get("/v1/agents/{agent_id}/branches/{branch_id}/checkpoints", response_model=list[CheckpointResponse])
def list_checkpoints(agent_id: str, branch_id: str, http_request: Request) -> list[CheckpointResponse]:
    require_branch(storage(http_request)._conn, agent_id, branch_id)
    rows = storage(http_request)._conn.execute(
        """
        SELECT checkpoint_id, agent_id, session_id, branch_id, run_id, kind, name, message_cursor, metadata
        FROM checkpoints
        WHERE agent_id = ? AND branch_id = ?
        ORDER BY created_at ASC
        """,
        (agent_id, branch_id),
    ).fetchall()
    return [checkpoint_response(row) for row in rows]
