from __future__ import annotations

import os
import signal
import subprocess
import threading

from fastapi import APIRouter, Request

from agent_home.errors import raise_error
from agent_home.models import WorkspaceCommandRequest, WorkspaceCommandResponse
from agent_home.workspace_manager import ensure_workspace

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
