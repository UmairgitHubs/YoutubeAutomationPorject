from __future__ import annotations

import logging
import os
import struct
import subprocess
import sys
from pathlib import Path

from app.config import PROJECT_ROOT, Settings

log = logging.getLogger("puzmania.desktop")

ICO_PATH = PROJECT_ROOT / "frontend" / "puzmania.ico"
LAUNCHER = PROJECT_ROOT / "Puzmania.bat"


def create_desktop_shortcut(settings: Settings) -> dict:
    ico = ensure_icon()
    bat = ensure_launcher()
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    if not desktop.is_dir():
        desktop = Path.home() / "Desktop"
    shortcut = desktop / "Puzmania.lnk"
    target = str(bat)
    python = Path(sys.executable)
    workdir = str(PROJECT_ROOT)
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$s = $shell.CreateShortcut('{_ps(shortcut)}'); "
        f"$s.TargetPath = '{_ps(target)}'; "
        f"$s.WorkingDirectory = '{_ps(workdir)}'; "
        f"$s.WindowStyle = 1; "
        f"$s.Description = 'Open the Puzmania publishing desk'; "
        f"$s.IconLocation = '{_ps(ico)}'; "
        "$s.Save()"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not shortcut.exists():
        detail = (proc.stderr or proc.stdout or "PowerShell could not create the shortcut.").strip()
        raise ValueError(detail[:300])
    log.info("Desktop shortcut written to %s (python=%s)", shortcut, python)
    return {"ok": True, "path": str(shortcut), "launcher": str(bat)}


def ensure_launcher() -> Path:
    python = Path(sys.executable)
    launch_py = Path(__file__).resolve().parents[1] / "scripts" / "launch_desk.py"
    body = (
        "@echo off\n"
        "setlocal\n"
        f'cd /d "{PROJECT_ROOT}"\n'
        f'set "PY={python}"\n'
        "if not exist \"%PY%\" set PY=python\n"
        f'"{python}" "{launch_py}"\n'
        "if errorlevel 1 pause\n"
    )
    LAUNCHER.write_text(body, encoding="utf-8")
    return LAUNCHER


def ensure_icon() -> Path:
    if ICO_PATH.is_file() and ICO_PATH.stat().st_size > 100:
        return ICO_PATH
    ICO_PATH.parent.mkdir(parents=True, exist_ok=True)
    ICO_PATH.write_bytes(_gold_p_ico())
    return ICO_PATH


def _ps(path: Path | str) -> str:
    return str(path).replace("'", "''")


def _gold_p_ico(size: int = 32) -> bytes:
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = 14, 13, 11, 255
            # gold frame
            if x < 2 or y < 2 or x >= size - 2 or y >= size - 2:
                r, g, b = 212, 160, 86
            # simple P
            in_stem = 8 <= x <= 12 and 8 <= y <= 24
            in_bowl = 12 <= x <= 22 and 8 <= y <= 16 and (x <= 13 or y <= 9 or y >= 15 or x >= 21)
            if in_stem or in_bowl:
                r, g, b = 232, 196, 138
            xor.extend((b, g, r, a))
    and_row = ((size + 31) // 32) * 4
    mask = bytes(and_row * size)
    dib = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(xor),
        0,
        0,
        0,
        0,
    )
    image = dib + bytes(xor) + mask
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(image), 6 + 16)
    return header + entry + image
