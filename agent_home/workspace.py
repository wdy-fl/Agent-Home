from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from agent_home.errors import raise_error
from agent_home.models import WorkspaceCommandRequest, WorkspaceCommandResponse

router = APIRouter()
MAX_OBJECT_SIZE = 1_000_000
_AGENT_COMMAND_LOCKS: dict[str, threading.Lock] = {}
_AGENT_COMMAND_LOCKS_GUARD = threading.Lock()


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


def execution_root(request: Request, agent_id: str) -> Path:
    root = request.app.state.settings.execution_root / safe_agent_dir(agent_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def command_lock(agent_id: str) -> threading.Lock:
    with _AGENT_COMMAND_LOCKS_GUARD:
        lock = _AGENT_COMMAND_LOCKS.get(agent_id)
        if lock is None:
            lock = threading.Lock()
            _AGENT_COMMAND_LOCKS[agent_id] = lock
        return lock


def path_under_root(root: Path, logical_path: str) -> Path:
    destination = root / logical_path.lstrip("/")
    resolved_root = root.resolve()
    resolved_destination = destination.resolve()
    if not resolved_destination.is_relative_to(resolved_root):
        raise_error("invalid_path", "path must stay under execution root")
    return destination


def object_metadata(row: sqlite3.Row) -> dict[str, object]:
    metadata = cast(dict[str, Any], json.loads(cast(str, row["metadata"])))
    return {
        "agent_id": row["agent_id"],
        "object_id": row["object_id"],
        "path": row["path"],
        "kind": row["kind"],
        "content_type": row["content_type"],
        "size": row["size"],
        "content_hash": row["content_hash"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def workspace_rows(request: Request, agent_id: str):
    return storage(request)._conn.execute(
        """
        SELECT path, storage_key, content_hash
        FROM workspace_objects
        WHERE agent_id = ? AND kind = ?
        ORDER BY path ASC
        """,
        (agent_id, "file"),
    ).fetchall()


def materialize_workspace(request: Request, agent_id: str) -> dict[str, str]:
    root = execution_root(request, agent_id)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    hashes = {}
    blobs = object_root(request, agent_id)
    for row in workspace_rows(request, agent_id):
        logical_path = validate_path(cast(str, row["path"]))
        hashes[logical_path] = cast(str, row["content_hash"])
        destination = path_under_root(root, logical_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        storage_key = cast(str, row["storage_key"])
        shutil.copyfile(blobs / storage_key, destination)
    return hashes


def upsert_object_bytes(request: Request, agent_id: str, path: str, body: bytes) -> None:
    path = validate_path(path)
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
                old_storage_key = cast(str, old["storage_key"])
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
    except Exception:
        blob_path.unlink(missing_ok=True)
        raise

    if old_storage_key:
        old_blob_path = object_root(request, agent_id) / old_storage_key
        if old_blob_path.exists():
            old_blob_path.unlink()


def delete_object_bytes(request: Request, agent_id: str, path: str) -> None:
    path = validate_path(path)
    storage_key = None
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
            return
        storage_key = cast(str, row["storage_key"])
        connection.execute(
            """
            DELETE FROM workspace_objects
            WHERE agent_id = ? AND path = ?
            """,
            (agent_id, path),
        )
    blob_path = object_root(request, agent_id) / storage_key
    if blob_path.exists():
        blob_path.unlink()


def sync_workspace_changes(request: Request, agent_id: str, previous_hashes: dict[str, str]) -> list[str]:
    root = execution_root(request, agent_id)
    changed_paths = []
    seen_paths = set()
    for file_path in root.rglob("*"):
        if not file_path.is_file() or file_path.is_symlink():
            continue
        logical_path = validate_path("/" + file_path.relative_to(root).as_posix())
        seen_paths.add(logical_path)
        body = file_path.read_bytes()
        content_hash = hashlib.sha256(body).hexdigest()
        if previous_hashes.get(logical_path) == content_hash:
            continue
        upsert_object_bytes(request, agent_id, logical_path, body)
        changed_paths.append(logical_path)
    for logical_path in previous_hashes:
        if logical_path not in seen_paths:
            delete_object_bytes(request, agent_id, logical_path)
            changed_paths.append(logical_path)
    return sorted(changed_paths)


@router.post("/v1/agents/{agent_id}/workspace/commands", response_model=WorkspaceCommandResponse)
def run_workspace_command(agent_id: str, command: WorkspaceCommandRequest, request: Request) -> WorkspaceCommandResponse:
    with command_lock(agent_id):
        previous_hashes = materialize_workspace(request, agent_id)
        root = execution_root(request, agent_id)
        env = os.environ.copy()
        env.update(command.env)
        use_process_group = hasattr(os, "killpg")
        process = subprocess.Popen(
            command.command,
            cwd=root,
            env=env,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=use_process_group,
        )
        try:
            stdout, stderr = process.communicate(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired:
            if use_process_group:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            raise_error("command_timeout", f"command timed out after {command.timeout_seconds} seconds")

        changed_paths = sync_workspace_changes(request, agent_id, previous_hashes)
        return WorkspaceCommandResponse(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            changed_paths=changed_paths,
        )


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
                old_storage_key = cast(str, old["storage_key"])
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
            if row is None:
                raise_error("path_not_found", f"path {path} not found")
    except Exception:
        blob_path.unlink(missing_ok=True)
        raise

    row = cast(sqlite3.Row, row)
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
    row = cast(sqlite3.Row, row)
    blob_path = object_root(request, agent_id) / cast(str, row["storage_key"])
    if not blob_path.exists():
        raise_error("path_not_found", f"path {path} not found")
    return FileResponse(blob_path, media_type=cast(str, row["content_type"]))


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
        row = cast(sqlite3.Row, row)
        storage_key = cast(str, row["storage_key"])
        connection.execute(
            """
            DELETE FROM workspace_objects
            WHERE agent_id = ? AND path = ?
            """,
            (agent_id, path),
        )
    blob_path = object_root(request, agent_id) / storage_key
    if blob_path.exists():
        blob_path.unlink()
    return Response(status_code=204)
