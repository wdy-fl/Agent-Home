from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class CheckpointKind(str, Enum):
    user_snapshot = "user_snapshot"
    runtime = "runtime"


class CreateAgentRequest(BaseModel):
    agent_id: str = Field(min_length=1)


class AgentConfig(BaseModel):
    memory: dict[str, Any] = Field(default_factory=lambda: {"auto_extract": {"enabled": False, "mode": "tiered"}})
    workspace: dict[str, Any] = Field(default_factory=lambda: {"max_object_size": 1_000_000})


class AgentCreatedResponse(BaseModel):
    agent_id: str
    token: str
    config: AgentConfig


class AgentResponse(BaseModel):
    agent_id: str
    config: AgentConfig


class CreateSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None
    active_branch_id: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    agent_id: str
    title: str
    metadata: dict[str, Any]
    active_branch_id: str


class BranchResponse(BaseModel):
    branch_id: str
    session_id: str
    parent_branch_id: str = ""
    fork_checkpoint_id: str = ""
    base_message_cursor: int = 0
    resume_head: str = ""


class CreateBranchRequest(BaseModel):
    branch_id: str | None = None
    parent_branch_id: str = ""
    fork_checkpoint_id: str = ""
    base_message_cursor: int = 0


class UpdateBranchRequest(BaseModel):
    resume_head: str | None = None


class CreateRunRequest(BaseModel):
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)


class RunResponse(BaseModel):
    run_id: str
    agent_id: str
    session_id: str
    branch_id: str
    status: RunStatus


class UpdateRunStatusRequest(BaseModel):
    status: RunStatus


class CreateMessageRequest(BaseModel):
    message_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    run_id: str | None = None
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class MessageResponse(CreateMessageRequest):
    agent_id: str
    sequence: int


class CreateCheckpointRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    run_id: str | None = None
    kind: CheckpointKind
    name: str
    message_cursor: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckpointResponse(BaseModel):
    checkpoint_id: str
    agent_id: str
    session_id: str
    branch_id: str
    run_id: str | None = None
    kind: CheckpointKind
    name: str
    message_cursor: int
    metadata: dict[str, Any]


class ResumeResponse(BaseModel):
    session_id: str
    branch_id: str
    messages: list[MessageResponse]
    interrupted_info: str | None = None


class RewindRequest(BaseModel):
    checkpoint_id: str


class RewindResponse(BaseModel):
    new_branch_id: str
    messages: list[MessageResponse]


class CreateMemoryRequest(BaseModel):
    type: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_session_id: str = ""
    source_message_ids: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    tags: list[str] | None = None


class MemoryResponse(BaseModel):
    memory_id: str
    agent_id: str
    type: str
    content: str
    tags: list[str]
    source: str
    source_session_id: str
    source_message_ids: list[str]
    confidence: float
    status: str


class ExtractionRequest(BaseModel):
    session_id: str
    trigger: str


class WorkspaceCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=120, gt=0, le=600)
    env: dict[str, str] = Field(default_factory=dict)


class WorkspaceCommandResponse(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    changed_paths: list[str]
