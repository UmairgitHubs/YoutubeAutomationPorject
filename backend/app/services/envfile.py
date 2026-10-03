from __future__ import annotations

import re

from app.config import PROJECT_ROOT


def upsert_env(key: str, value: str) -> None:
    path = PROJECT_ROOT / ".env"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")
