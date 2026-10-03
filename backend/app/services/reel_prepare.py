from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import PROJECT_ROOT
from app.services.ffmpeg import ffmpeg_exe

log = logging.getLogger("puzmania.reel_prepare")

PREP_DIR = PROJECT_ROOT / "data" / "ig_prepare"
PREP_VERSION = "cover1"
# Fill 9:16 (cover + crop). Padding with black is what made Reels look like a postage stamp on the grid.
REEL_VF = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


def prepare_instagram_reel(src: Path, episode_id: str) -> Path:
    """Remux to Instagram Reels spec: full-bleed 9:16 H.264, AAC, faststart."""
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        raise ValueError(
            "ffmpeg is not installed. On Windows run: winget install --id Gyan.FFmpeg -e "
            "then close the terminal, restart the server, and publish again."
        )

    dest = PREP_DIR / f"{episode_id}.{PREP_VERSION}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime and dest.stat().st_size > 10_000:
        log.info("Reusing prepared reel %s", dest.name)
        return dest

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-vf",
        REEL_VF,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-preset",
        "veryfast",
        "-b:v",
        "8M",
        "-maxrate",
        "10M",
        "-bufsize",
        "16M",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()[-400:]
        raise ValueError(detail)
    log.info("Prepared Instagram reel %s (%s bytes)", dest.name, dest.stat().st_size)
    return dest
