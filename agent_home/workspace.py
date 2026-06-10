from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from agent_home.errors import raise_error
from agent_home.models import (
    WorkspaceCommandRequest,
    WorkspaceCommandResponse,
    WorkspaceDeleteRequest,
    WorkspaceDeleteResponse,
    WorkspaceFileInfo,
    WorkspaceListRequest,
    WorkspaceListResponse,
    WorkspaceReadRequest,
    WorkspaceReadResponse,
    WorkspaceWriteRequest,
    WorkspaceWriteResponse,
)
from agent_home.workspace_manager import ensure_workspace, resolve_workspace_path

router = APIRouter()
_AGENT_COMMAND_LOCKS: dict[str, threading.Lock] = {}
_AGENT_COMMAND_LOCKS_GUARD = threading.Lock()


def command_lock(agent_id: str) -> threading.Lock:
    with _AGENT_COMMAND_LOCKS_GUARD:
        lock = _AGENT_COMMAND_LOCKS.get(agent_id)
        if lock is None:
            lock = threading.Lock()
            _AGENT_COMMAND_LOCKS[agent_id] = lock
        return lock


@router.post("/v1/agents/{agent_id}/workspace/commands", response_model=WorkspaceCommandResponse)
def run_workspace_command(agent_id: str, command: WorkspaceCommandRequest, request: Request) -> WorkspaceCommandResponse:
    with command_lock(agent_id):
        root = ensure_workspace(request.app.state.settings, agent_id)
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

        return WorkspaceCommandResponse(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )


def _max_size(request: Request) -> int:
    return request.app.state.settings.workspace_max_object_size


@router.post("/v1/agents/{agent_id}/workspace/files/list", response_model=WorkspaceListResponse)
def list_files(agent_id: str, body: WorkspaceListRequest, request: Request) -> WorkspaceListResponse:
    target = resolve_workspace_path(request.app.state.settings, agent_id, body.path)
    if not target.exists():
        raise_error("path_not_found", f"path not found: {body.path}")
    if not target.is_dir():
        raise_error("invalid_path", f"not a directory: {body.path}")
    entries: list[WorkspaceFileInfo] = []
    for entry in sorted(target.iterdir(), key=lambda e: (e.is_file(), e.name)):
        stat = entry.stat()
        entries.append(WorkspaceFileInfo(
            name=entry.name,
            type="file" if entry.is_file() else "dir",
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        ))
    return WorkspaceListResponse(entries=entries)


@router.post("/v1/agents/{agent_id}/workspace/files/read", response_model=WorkspaceReadResponse)
def read_file(agent_id: str, body: WorkspaceReadRequest, request: Request) -> WorkspaceReadResponse:
    target = resolve_workspace_path(request.app.state.settings, agent_id, body.path)
    if not target.exists():
        raise_error("path_not_found", f"file not found: {body.path}")
    if not target.is_file():
        raise_error("invalid_path", f"not a file: {body.path}")
    max_size = _max_size(request)
    if target.stat().st_size > max_size:
        raise_error("object_too_large", f"file exceeds max size of {max_size} bytes")
    content = target.read_text(encoding="utf-8")
    return WorkspaceReadResponse(content=content, size=len(content.encode()))


@router.post("/v1/agents/{agent_id}/workspace/files/write", response_model=WorkspaceWriteResponse)
def write_file(agent_id: str, body: WorkspaceWriteRequest, request: Request) -> WorkspaceWriteResponse:
    max_size = _max_size(request)
    content_bytes = body.content.encode()
    if len(content_bytes) > max_size:
        raise_error("object_too_large", f"content exceeds max size of {max_size} bytes")
    target = resolve_workspace_path(request.app.state.settings, agent_id, body.path)
    if target.is_dir():
        raise_error("invalid_path", f"cannot overwrite a directory: {body.path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return WorkspaceWriteResponse(path=body.path, size=len(content_bytes))


@router.post("/v1/agents/{agent_id}/workspace/files/delete", response_model=WorkspaceDeleteResponse)
def delete_file(agent_id: str, body: WorkspaceDeleteRequest, request: Request) -> WorkspaceDeleteResponse:
    target = resolve_workspace_path(request.app.state.settings, agent_id, body.path)
    if not target.exists():
        raise_error("path_not_found", f"path not found: {body.path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return WorkspaceDeleteResponse(deleted=True)
