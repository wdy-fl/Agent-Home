from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from agent_home.errors import raise_error

router = APIRouter()
MAX_OBJECT_SIZE = 1_000_000


def storage(request: Request):
    return request.app.state.storage


def validate_path(path: str) -> str:
    parts = Path(path).parts
    if not path.startswith("/") or ".." in parts:
        raise_error("invalid_path", "path must be absolute and cannot contain '..'")
    return path


def safe_agent_dir(agent_id: str) -> str:
    return hashlib.sha256(agent_id.encode()).hexdigest()


def object_root(request: Request, agent_id: str) -> Path:
    root = request.app.state.settings.object_root / safe_agent_dir(agent_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def object_metadata(row) -> dict[str, object]:
    return {
        "agent_id": row["agent_id"],
        "object_id": row["object_id"],
        "path": row["path"],
        "kind": row["kind"],
        "content_type": row["content_type"],
        "size": row["size"],
        "content_hash": row["content_hash"],
        "metadata": json.loads(row["metadata"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.put("/v1/agents/{agent_id}/workspace/object")
async def put_object(agent_id: str, path: str, request: Request) -> dict[str, object]:
    path = validate_path(path)
    body = await request.body()
    if len(body) > MAX_OBJECT_SIZE:
        raise_error("object_too_large", "object exceeds maximum size")

    object_id = str(uuid4())
    content_hash = hashlib.sha256(body).hexdigest()
    storage_key = f"{object_id}.blob"
    blob_path = object_root(request, agent_id) / storage_key
    blob_path.write_bytes(body)

    old_storage_key = None
    try:
        with storage(request).transaction() as connection:
            old = connection.execute(
                """
                SELECT storage_key
                FROM workspace_objects
                WHERE agent_id = ? AND path = ?
                """,
                (agent_id, path),
            ).fetchone()
            if old is not None:
                old_storage_key = old["storage_key"]
                connection.execute(
                    """
                    UPDATE workspace_objects
                    SET object_id = ?, kind = ?, content_type = ?, size = ?, content_hash = ?, storage_key = ?, metadata = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE agent_id = ? AND path = ?
                    """,
                    (object_id, "file", "application/octet-stream", len(body), content_hash, storage_key, json.dumps({}), agent_id, path),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO workspace_objects(agent_id, object_id, path, kind, content_type, size, content_hash, storage_key, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (agent_id, object_id, path, "file", "application/octet-stream", len(body), content_hash, storage_key, json.dumps({})),
                )
            row = connection.execute(
                """
                SELECT agent_id, object_id, path, kind, content_type, size, content_hash, storage_key, metadata, created_at, updated_at
                FROM workspace_objects
                WHERE agent_id = ? AND path = ?
                """,
                (agent_id, path),
            ).fetchone()
    except Exception:
        blob_path.unlink(missing_ok=True)
        raise

    if old_storage_key:
        old_blob_path = object_root(request, agent_id) / old_storage_key
        if old_blob_path.exists():
            old_blob_path.unlink()
    return object_metadata(row)


@router.get("/v1/agents/{agent_id}/workspace/object")
def get_object(agent_id: str, path: str, request: Request) -> FileResponse:
    path = validate_path(path)
    row = storage(request)._conn.execute(
        """
        SELECT storage_key, content_type
        FROM workspace_objects
        WHERE agent_id = ? AND path = ?
        """,
        (agent_id, path),
    ).fetchone()
    if row is None:
        raise_error("path_not_found", f"path {path} not found")
    blob_path = object_root(request, agent_id) / row["storage_key"]
    if not blob_path.exists():
        raise_error("path_not_found", f"path {path} not found")
    return FileResponse(blob_path, media_type=row["content_type"])


@router.get("/v1/agents/{agent_id}/workspace/objects")
def list_objects(agent_id: str, prefix: str, request: Request) -> list[dict[str, object]]:
    prefix = validate_path(prefix)
    rows = storage(request)._conn.execute(
        """
        SELECT agent_id, object_id, path, kind, content_type, size, content_hash, storage_key, metadata, created_at, updated_at
        FROM workspace_objects
        WHERE agent_id = ? AND path LIKE ?
        ORDER BY path ASC
        """,
        (agent_id, f"{prefix}%"),
    ).fetchall()
    return [object_metadata(row) for row in rows]


@router.delete("/v1/agents/{agent_id}/workspace/object", status_code=204)
def delete_object(agent_id: str, path: str, request: Request) -> Response:
    path = validate_path(path)
    with storage(request).transaction() as connection:
        row = connection.execute(
            """
            SELECT storage_key
            FROM workspace_objects
            WHERE agent_id = ? AND path = ?
            """,
            (agent_id, path),
        ).fetchone()
        if row is None:
            raise_error("path_not_found", f"path {path} not found")
        connection.execute(
            """
            DELETE FROM workspace_objects
            WHERE agent_id = ? AND path = ?
            """,
            (agent_id, path),
        )
    blob_path = object_root(request, agent_id) / row["storage_key"]
    if blob_path.exists():
        blob_path.unlink()
    return Response(status_code=204)
