from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    metadata TEXT NOT NULL,
    active_branch_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, session_id)
);

CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    parent_branch_id TEXT NOT NULL,
    fork_checkpoint_id TEXT NOT NULL,
    base_message_cursor INTEGER NOT NULL,
    resume_head TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, run_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_calls TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, message_id),
    UNIQUE (agent_id, branch_id, sequence)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    run_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    message_cursor INTEGER NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    source TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    source_message_ids TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, memory_id)
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL,
    source_message_ids TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_id, candidate_id)
);
"""


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def create_agent(self, agent_id: str, token_hash: str, config: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO agents(agent_id, token_hash, config)
                VALUES (?, ?, ?)
                """,
                (agent_id, token_hash, json.dumps(config)),
            )

    def get_agent(self, agent_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT agent_id, token_hash, config
            FROM agents
            WHERE agent_id = ?
            """,
            (agent_id,),
        ).fetchone()

    def update_agent_config(self, agent_id: str, config: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE agents
                SET config = ?, updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ?
                """,
                (json.dumps(config), agent_id),
            )
