from __future__ import annotations

import re
from pathlib import Path

from agent_home.config import Settings
from agent_home.errors import raise_error

_AGENT_ID_PATTERN = re.compile(r"^[0-9]+$")


def validate_workspace_agent_id(agent_id: str) -> str:
    if not _AGENT_ID_PATTERN.fullmatch(agent_id):
        raise_error("invalid_agent_id", "agent_id must contain digits only")
    return agent_id


def workspace_dir(settings: Settings, agent_id: str) -> Path:
    safe_agent_id = validate_workspace_agent_id(agent_id)
    return settings.workspace_root / safe_agent_id


AGENT_MD_TEMPLATE = """\
# Agent {agent_id}

## 身份设定

你是 Agent {agent_id}，运行在 Agent-Home 平台上。你拥有独立的工作区目录，
可以执行 shell 命令、读写文件、通过提供的工具与外部系统交互。

## 行为指导

- 完整完成任务：在声称完成之前，务必验证你的工作成果。
- 保持简洁：回复应简短直接，避免不必要的解释。
- 先思考再行动：将复杂问题拆解为可执行的步骤。
- 优雅处理错误：遇到失败时，先诊断根因并尝试修复，不要轻易放弃。
- 尊重工作区边界：在工作区目录内合理组织文件。除非明确指示，不要读取或
  修改工作区之外的文件。
- 坦诚面对不确定性：如果不知道答案或缺乏必要上下文，直接说明而非猜测。

## 工作区

你的工作区位于 `.workspace/{agent_id}/`。默认情况下，所有文件操作应限制
在此目录内。按需使用子目录组织你的工作。
"""


def ensure_workspace(settings: Settings, agent_id: str) -> Path:
    path = workspace_dir(settings, agent_id)
    path.mkdir(parents=True, exist_ok=True)
    agent_md = path / "AGENT.md"
    if not agent_md.exists():
        agent_md.write_text(AGENT_MD_TEMPLATE.format(agent_id=agent_id))
    return path


def resolve_workspace_path(settings: Settings, agent_id: str, relative_path: str) -> Path:
    """Resolve a relative path within the agent's workspace, preventing path traversal."""
    root = ensure_workspace(settings, agent_id).resolve()
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise_error("invalid_path", "path escapes workspace")
    return resolved
