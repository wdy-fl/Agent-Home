from __future__ import annotations

import uvicorn

from agent_home.app import create_app

app = create_app()


def main() -> None:
    uvicorn.run("agent_home.main:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
