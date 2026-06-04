from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Request, Response

from agent_home.errors import raise_error
from agent_home.models import CreateMemoryRequest, ExtractionRequest, MemoryResponse, UpdateMemoryRequest

router = APIRouter()


def storage(request: Request):
    return request.app.state.storage


def row_to_memory(row) -> MemoryResponse:
    return MemoryResponse(
        memory_id=row["memory_id"],
        agent_id=row["agent_id"],
        type=row["type"],
        content=row["content"],
        tags=json.loads(row["tags"]),
        source=row["source"],
        source_session_id=row["source_session_id"],
        source_message_ids=json.loads(row["source_message_ids"]),
        confidence=row["confidence"],
        status=row["status"],
    )


def get_active_memory(connection, agent_id: str, memory_id: str):
    row = connection.execute(
        """
        SELECT memory_id, agent_id, type, content, tags, source, source_session_id, source_message_ids, confidence, status
        FROM memories
        WHERE agent_id = ? AND memory_id = ? AND status != 'deleted'
        """,
        (agent_id, memory_id),
    ).fetchone()
    if row is None:
        raise_error("memory_not_found", f"memory {memory_id} not found")
    return row


@router.post("/v1/agents/{agent_id}/memory", response_model=MemoryResponse)
def create_memory(agent_id: str, request: CreateMemoryRequest, http_request: Request) -> MemoryResponse:
    memory_id = str(uuid4())
    with storage(http_request).transaction() as connection:
        connection.execute(
            """
            INSERT INTO memories(memory_id, agent_id, type, content, tags, source, source_session_id, source_message_ids, confidence, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                agent_id,
                request.type,
                request.content,
                json.dumps(request.tags),
                "manual",
                request.source_session_id,
                json.dumps(request.source_message_ids),
                request.confidence,
                "active",
            ),
        )
    return row_to_memory(get_active_memory(storage(http_request)._conn, agent_id, memory_id))


@router.get("/v1/agents/{agent_id}/memory/search", response_model=list[MemoryResponse])
def search_memories(agent_id: str, q: str, http_request: Request, type: str | None = None) -> list[MemoryResponse]:
    clauses = ["agent_id = ?", "status = 'active'", "content LIKE ?"]
    values: list[object] = [agent_id, f"%{q}%"]
    if type is not None:
        clauses.append("type = ?")
        values.append(type)
    rows = storage(http_request)._conn.execute(
        f"""
        SELECT memory_id, agent_id, type, content, tags, source, source_session_id, source_message_ids, confidence, status
        FROM memories
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at ASC
        """,
        values,
    ).fetchall()
    return [row_to_memory(row) for row in rows]


@router.get("/v1/agents/{agent_id}/memory/{memory_id}", response_model=MemoryResponse)
def get_memory(agent_id: str, memory_id: str, http_request: Request) -> MemoryResponse:
    return row_to_memory(get_active_memory(storage(http_request)._conn, agent_id, memory_id))


@router.patch("/v1/agents/{agent_id}/memory/{memory_id}", response_model=MemoryResponse)
def update_memory(agent_id: str, memory_id: str, request: UpdateMemoryRequest, http_request: Request) -> MemoryResponse:
    with storage(http_request).transaction() as connection:
        get_active_memory(connection, agent_id, memory_id)
        updates: list[str] = []
        values: list[object] = []
        if request.content is not None:
            updates.append("content = ?")
            values.append(request.content)
        if request.tags is not None:
            updates.append("tags = ?")
            values.append(json.dumps(request.tags))
        if updates:
            values.extend([agent_id, memory_id])
            connection.execute(
                f"""
                UPDATE memories
                SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND memory_id = ?
                """,
                values,
            )
    return row_to_memory(get_active_memory(storage(http_request)._conn, agent_id, memory_id))


@router.delete("/v1/agents/{agent_id}/memory/{memory_id}", status_code=204)
def delete_memory(agent_id: str, memory_id: str, http_request: Request) -> Response:
    with storage(http_request).transaction() as connection:
        get_active_memory(connection, agent_id, memory_id)
        connection.execute(
            """
            UPDATE memories
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ? AND memory_id = ?
            """,
            (agent_id, memory_id),
        )
    return Response(status_code=204)


@router.post("/v1/agents/{agent_id}/memory/extractions")
def extract_memories(agent_id: str, request: ExtractionRequest, http_request: Request) -> dict[str, list[object]]:
    row = storage(http_request).get_agent(agent_id)
    config = json.loads(row["config"])
    if not config.get("memory", {}).get("auto_extract", {}).get("enabled", False):
        raise_error("auto_extract_disabled", "memory auto extraction is disabled")
    return {"created_memories": [], "candidates": []}
