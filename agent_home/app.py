from __future__ import annotations

import json
import secrets
import sqlite3

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from agent_home.auth import hash_token, new_token, verify_bearer_token
from agent_home.config import Settings, default_settings
from agent_home.errors import raise_error
from agent_home.memory import router as memory_router
from agent_home.models import AgentConfig, AgentCreatedResponse, AgentResponse, CreateAgentRequest
from agent_home.storage import Storage
from agent_home.timeline import router as timeline_router
from agent_home.workspace import router as workspace_router
from agent_home.workspace_manager import ensure_workspace


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Agent-Home", version="0.1.0")
    app.state.settings = settings or default_settings()
    app.state.storage = Storage(app.state.settings.database_path)

    @app.exception_handler(HTTPException)
    def http_exception_handler(_request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def require_agent(agent_id: str, token: str = Depends(verify_bearer_token)) -> sqlite3.Row:
        row = app.state.storage.get_agent(agent_id)
        if row is None or not secrets.compare_digest(row["token_hash"], hash_token(token)):
            raise_error("auth_failed", "invalid agent token")
        return row

    @app.post("/v1/agents", response_model=AgentCreatedResponse, status_code=201)
    def create_agent(request: CreateAgentRequest) -> AgentCreatedResponse:
        ensure_workspace(app.state.settings, request.agent_id)
        token = new_token()
        config = AgentConfig()
        try:
            app.state.storage.create_agent(
                request.agent_id,
                hash_token(token),
                config.model_dump(),
            )
        except sqlite3.IntegrityError:
            raise_error("agent_exists", f"agent {request.agent_id} already exists")
        return AgentCreatedResponse(agent_id=request.agent_id, token=token, config=config)

    @app.get("/v1/agents/{agent_id}", response_model=AgentResponse)
    def get_agent(agent_id: str, row: sqlite3.Row = Depends(require_agent)) -> AgentResponse:
        return AgentResponse(agent_id=agent_id, config=AgentConfig(**json.loads(row["config"])))

    app.include_router(timeline_router, dependencies=[Depends(require_agent)])
    app.include_router(workspace_router, dependencies=[Depends(require_agent)])
    app.include_router(memory_router, dependencies=[Depends(require_agent)])
    return app
