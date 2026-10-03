"""Start the Puzmania desk and open it in the browser."""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.config import get_settings  # noqa: E402


def _open(url: str) -> None:
    time.sleep(1.4)
    webbrowser.open(url)


def main() -> None:
    settings = get_settings()
    url = f"http://{settings.host}:{settings.port}/"
    threading.Thread(target=_open, args=(url,), daemon=True).start()
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
