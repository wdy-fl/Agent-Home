from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi.testclient import TestClient

from agent_home.app import create_app


def test_health_endpoint_returns_ok():
    app = create_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_main_exports_app():
    from agent_home.main import app

    assert app.title == "Agent-Home"


def test_local_daemon_serves_health_endpoint():
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)
    process = subprocess.Popen(
        [sys.executable, "-m", "agent_home.main"],
        cwd=project_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 10
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=0.5) as response:
                    assert response.read() == b'{"status":"ok"}'
                    return
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                time.sleep(0.1)
        raise AssertionError(f"daemon did not serve /health: {last_error}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
