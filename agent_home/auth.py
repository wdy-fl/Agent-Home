from __future__ import annotations

import hashlib
import secrets

from fastapi import Header

from agent_home.errors import raise_error


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise_error("auth_failed", "missing bearer token")
    return authorization.removeprefix("Bearer ").strip()
