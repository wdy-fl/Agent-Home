from __future__ import annotations

from typing import Any

from fastapi import HTTPException

ERROR_STATUS = {
    "auth_failed": 401,
    "agent_not_found": 404,
    "agent_exists": 409,
    "invalid_agent_id": 400,
    "session_exists": 409,
    "run_exists": 409,
    "message_exists": 409,
    "checkpoint_exists": 409,
    "session_not_found": 404,
    "branch_not_found": 404,
    "run_not_found": 404,
    "checkpoint_not_found": 404,
    "invalid_checkpoint_kind": 400,
    "invalid_message_cursor": 400,
    "path_not_found": 404,
    "path_conflict": 409,
    "invalid_path": 400,
    "object_too_large": 413,
    "command_timeout": 408,
    "memory_not_found": 404,
    "memory_conflict": 409,
    "auto_extract_disabled": 409,
}


def raise_error(code: str, message: str, details: dict[str, Any] | None = None) -> None:
    status = ERROR_STATUS.get(code, 400)
    raise HTTPException(status_code=status, detail={"error": {"code": code, "message": message, "details": details or {}}})
