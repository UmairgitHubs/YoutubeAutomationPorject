from __future__ import annotations

import logging
import re
import subprocess
from datetime import date
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.models import Episode, FeaturedWinner
from app.services.ffmpeg import ffmpeg_exe, ffprobe_exe

log = logging.getLogger("puzmania.overlay")

INTRO_SECONDS = 5.0
RENDER_DIR = PROJECT_ROOT / "data" / "renders"
WINNER_DIR = PROJECT_ROOT / "data" / "winner"
LOGO_PATH = PROJECT_ROOT / "data" / "brand" / "puzmania-logo.png"
SCALE_VF = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
TEMPLATE_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
# Titles start 2s in; the closing CTA holds at least 2s through the end of the clip.
TEXT_START = 2.0
CTA_SECONDS = 2.0
TEXT_SCALE0 = 0.82
ZOOM_SECONDS = 0.8


def winner_template() -> Path | None:
    """First video in data/winner — the dummy pre-roll under the winner titles."""
    if not WINNER_DIR.is_dir():
        return None
    clips = sorted(
        (path for path in WINNER_DIR.iterdir() if path.is_file() and path.suffix.lower() in TEMPLATE_EXTS),
        key=lambda path: path.name.lower(),
    )
    return clips[0] if clips else None


def display_name(username: str) -> str:
    name = re.sub(r"[^\w\s.-]", "", (username or "a member").strip()) or "a member"
    return name.replace("_", " ").upper()


def format_points_label(points: int) -> str:
    return f"{int(points):,}".replace(",", ".") + " pts"


def _mid_fontsize(name: str) -> int:
    n = max(1, len((name or "").strip()))
    if n <= 8:
        return 150
    if n <= 12:
        return 118
    if n <= 16:
        return 92
    return 72


def prepare_publish_video(
    episode: Episode,
    settings: Settings,
    winner: FeaturedWinner | None,
    slot_date: date | None = None,
) -> Path | None:
    """On winner days, prepend the dummy clip with large winner titles over the video."""
    from app.publishers.base import Publisher

    source = Publisher().video_path(episode)
    if source is None or not source.exists():
        return None
    if winner is None:
        return None
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        log.warning("ffmpeg missing — publishing without the winner intro")
        return source

    dest = RENDER_DIR / f"{episode.episode_id}.winner.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = dest.with_suffix(".txt")
    template = winner_template()
    tpl_stamp = f"{template.name}|{template.stat().st_mtime_ns}" if template else "generated"
    stamp = f"{winner.username}|{winner.points}|{source.stat().st_mtime_ns}|{tpl_stamp}|text2s"
    if dest.is_file() and dest.stat().st_size > 20_000 and marker.is_file() and marker.read_text(encoding="utf-8") == stamp:
        return dest

    intro = RENDER_DIR / f"{episode.episode_id}.intro.mp4"
    body = RENDER_DIR / f"{episode.episode_id}.body.mp4"
    try:
        if not _render_intro(ffmpeg, intro, winner, slot_date or episode.scheduled_date or date.today()):
            log.warning("Winner intro render failed for %s", episode.episode_id)
            return source
        if not _encode_body(ffmpeg, source, body):
            log.warning("Could not scale %s for concat", episode.episode_id)
            return source
        if not _concat(ffmpeg, intro, body, dest):
            dest.unlink(missing_ok=True)
            log.warning("Could not prepend winner intro for %s", episode.episode_id)
            return source
        marker.write_text(stamp, encoding="utf-8")
        return dest
    finally:
        intro.unlink(missing_ok=True)
        body.unlink(missing_ok=True)


def _render_intro(ffmpeg: str, dest: Path, winner: FeaturedWinner, slot_date: date) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    template = winner_template()
    if template and _render_template_intro(ffmpeg, dest, template, winner):
        return True
    if template:
        log.warning("Could not write the winner name onto %s — using the generated card", template.name)
    font = _font_file()
    logo = _ensure_logo(ffmpeg, font)
    headline = "This weeks winner is"
    name = (winner.username or "a member").strip()
    points = f"with {int(winner.points)}"
    brand = "PUZMANIA"
    date_line = slot_date.strftime("%d %b %Y")

    attempts: list[list[str]] = []
    if logo:
        attempts.append(_intro_cmd(ffmpeg, dest, font, logo, headline, name, points, brand, date_line, animated=True))
        attempts.append(_intro_cmd(ffmpeg, dest, font, logo, headline, name, points, brand, date_line, animated=False))
    attempts.append(_intro_cmd(ffmpeg, dest, font, None, headline, name, points, brand, date_line, animated=True))
    attempts.append(_intro_cmd(ffmpeg, dest, font, None, headline, name, points, brand, date_line, animated=False))

    for cmd in attempts:
        dest.unlink(missing_ok=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 10_000:
            return True
        log.info("Intro attempt failed: %s", (proc.stderr or "")[-240:])
    dest.unlink(missing_ok=True)
    return False


def _intro_cmd(
    ffmpeg: str,
    dest: Path,
    font: str | None,
    logo: Path | None,
    headline: str,
    name: str,
    points: str,
    brand: str,
    date_line: str,
    *,
    animated: bool,
) -> list[str]:
    bg = "color=c=0x120e0a:s=1080x1920:d=5:r=30"
    if animated:
        bg_chain = "hue=h=t*28:s=1.2,eq=contrast=1.06:brightness=0.03"
    else:
        bg_chain = "eq=contrast=1.04:brightness=0.02"
    texts = [
        _drawtext(headline, font, 42, "h*0.52", "0xD4A056"),
        _drawtext(name, font, 72, "h*0.58", "0xF3EEE4"),
        _drawtext(points, font, 48, "h*0.68", "0xD4A056"),
        _drawtext(brand, font, 28, "h*0.86", "0xF3EEE4"),
        _drawtext(date_line, font, 22, "h*0.90", "0x9A9488"),
    ]
    text_chain = ",".join(part for part in texts if part)
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", bg, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if logo:
        cmd += ["-i", str(logo)]
        fade = "format=rgba,fade=t=in:st=0:d=0.45:alpha=1" if animated else "format=rgba"
        vf = (
            f"[0:v]{bg_chain},format=yuv420p[bg];"
            f"[2:v]scale=340:-1,{fade}[lg];"
            f"[bg][lg]overlay=(W-w)/2:360[v0];"
            f"[v0]{text_chain}[v]"
        )
        cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a:0"]
    else:
        vf = f"{bg_chain},{text_chain}"
        cmd += ["-vf", vf, "-map", "0:v:0", "-map", "1:a:0"]
    cmd += [
        "-t",
        "5",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-r",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    return cmd


def _render_template_intro(ffmpeg: str, dest: Path, template: Path, winner: FeaturedWinner) -> bool:
    phase1 = dest.with_name(dest.stem + ".oval1.png")
    phase2 = dest.with_name(dest.stem + ".oval2.png")
    try:
        duration = _media_duration(template) or 10.0
        if not _write_oval_text_card(ffmpeg, phase1, _winner_phase_lines(winner)):
            return False
        if not _write_oval_text_card(ffmpeg, phase2, _cta_phase_lines()):
            return False
        switch_at = _text_switch_at(duration)
        # Large titles from 2s, then CTA for the last 2s through the end.
        scale = (
            f"if(lt(t\\,{TEXT_START:.2f})\\,0.01\\,"
            f"min(1\\,{TEXT_SCALE0}+(1-{TEXT_SCALE0})*"
            f"min(1\\,(t-{TEXT_START:.2f})/{ZOOM_SECONDS:.2f})))"
        )
        sized = f"scale=w='trunc(iw*{scale}/2)*2':h='trunc(ih*{scale}/2)*2':eval=frame"
        place = "x='(W-w)/2':y='(H-h)/2'"
        vf = (
            f"[0:v]{SCALE_VF},fps=30,format=yuv420p[base];"
            f"[1:v]format=rgba,{sized}[c1];"
            f"[2:v]format=rgba,{sized}[c2];"
            f"[base][c1]overlay={place}:enable='gte(t,{TEXT_START:.2f})*lt(t,{switch_at:.2f})'[v1];"
            f"[v1][c2]overlay={place}:enable='gte(t,{switch_at:.2f})':shortest=1[v]"
        )
        encode = [
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
            "-r", "30", "-movflags", "+faststart",
        ]
        dest.unlink(missing_ok=True)
        with_audio = [
            ffmpeg, "-y", "-i", str(template),
            "-loop", "1", "-i", str(phase1),
            "-loop", "1", "-i", str(phase2),
            "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0",
            *encode, "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-t", f"{duration:.2f}", "-shortest", str(dest),
        ]
        proc = subprocess.run(with_audio, capture_output=True, text=True)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 20_000:
            return True
        dest.unlink(missing_ok=True)
        silent = [
            ffmpeg, "-y", "-i", str(template),
            "-loop", "1", "-i", str(phase1),
            "-loop", "1", "-i", str(phase2),
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", vf, "-map", "[v]", "-map", "3:a:0",
            *encode, "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-t", f"{duration:.2f}", "-shortest", str(dest),
        ]
        proc = subprocess.run(silent, capture_output=True, text=True)
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 20_000:
            return True
        dest.unlink(missing_ok=True)
        log.info("Template intro failed: %s", (proc.stderr or "")[-400:])
        return False
    finally:
        phase1.unlink(missing_ok=True)
        phase2.unlink(missing_ok=True)


def _text_switch_at(duration: float) -> float:
    """Winner copy from TEXT_START; CTA holds at least CTA_SECONDS through the end."""
    switch = float(duration) - CTA_SECONDS
    earliest = TEXT_START + 0.4
    latest = max(earliest, float(duration) - 0.05)
    return min(latest, max(earliest, switch))


def _winner_phase_lines(winner: FeaturedWinner) -> list[tuple[str, int, int]]:
    name = display_name(winner.username)
    return [
        ("WINNER of the WEEK", 72, 560),
        (name, _mid_fontsize(name), 790),
        (format_points_label(winner.points), 96, 1020),
    ]


def _cta_phase_lines() -> list[tuple[str, int, int]]:
    return [
        ("BE THE WINNER", 88, 760),
        ("SOLVE THIS NEXT PUZZLE", 64, 1040),
    ]


def _oval_fontsize(name: str) -> int:
    n = max(1, len((name or "").strip()))
    if n <= 6:
        return 108
    if n <= 8:
        return 90
    if n <= 12:
        return 68
    return 52


def _write_oval_text_card(ffmpeg: str, dest: Path, lines: list[tuple[str, int, int]]) -> bool:
    """Transparent full-frame card: large white titles with a heavy black stroke."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    font = _bold_font_file() or _font_file()
    font_part = f"fontfile='{_ffmpeg_path(font)}':" if font else ""
    draws = []
    for text, size, y in lines:
        if not text:
            continue
        stroke = 12 if int(size) >= 90 else 10
        draws.append(
            "drawtext="
            f"{font_part}"
            f"text='{_ffmpeg_text(text)}':"
            "x=(w-text_w)/2:"
            f"y={int(y)}:"
            f"fontsize={int(size)}:"
            "fontcolor=white:"
            f"borderw={stroke}:"
            "bordercolor=black:"
            "shadowx=6:shadowy=6:shadowcolor=black"
        )
    if not draws:
        return False
    cmd = [
        ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black@0.0:s=1080x1920:d=1,format=rgba",
        "-vf", ",".join(draws),
        "-frames:v", "1", "-update", "1",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 500:
        return True
    dest.unlink(missing_ok=True)
    log.info("Oval text card failed: %s", (proc.stderr or "")[-300:])
    return False


def _media_fps(path: Path) -> float:
    probe = ffprobe_exe()
    if not probe:
        return 24.0
    proc = subprocess.run(
        [
            probe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )
    raw = (proc.stdout or "").strip().splitlines()[0] if proc.stdout else ""
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            fps = float(num) / (float(den) or 1.0)
            if 1 <= fps <= 120:
                return fps
        except ValueError:
            pass
    try:
        fps = float(raw)
        if 1 <= fps <= 120:
            return fps
    except ValueError:
        pass
    return 24.0


def _media_duration(path: Path) -> float | None:
    probe = ffprobe_exe()
    if not probe:
        return None
    proc = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float((proc.stdout or "").strip())
    except ValueError:
        return None


def _encode_body(ffmpeg: str, source: Path, dest: Path) -> bool:
    common_v = [
        "-vf", SCALE_VF,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
        "-r", "30", "-movflags", "+faststart",
    ]
    with_audio = [
        ffmpeg, "-y", "-i", str(source), *common_v,
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(dest),
    ]
    proc = subprocess.run(with_audio, capture_output=True, text=True)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 10_000:
        return True
    dest.unlink(missing_ok=True)
    silent = [
        ffmpeg, "-y", "-i", str(source),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        *common_v,
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest", str(dest),
    ]
    proc = subprocess.run(silent, capture_output=True, text=True)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 10_000:
        return True
    dest.unlink(missing_ok=True)
    video_only = [ffmpeg, "-y", "-i", str(source), *common_v, "-an", str(dest)]
    proc = subprocess.run(video_only, capture_output=True, text=True)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 10_000:
        return True
    dest.unlink(missing_ok=True)
    log.info("Body encode failed: %s", (proc.stderr or "")[-240:])
    return False


def _concat(ffmpeg: str, intro: Path, body: Path, dest: Path) -> bool:
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(intro),
        "-i",
        str(body),
        "-filter_complex",
        "[0:v]fps=30,format=yuv420p[v0];[1:v]fps=30,format=yuv420p[v1];"
        "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a0];"
        "[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 20_000:
        return True
    dest.unlink(missing_ok=True)
    video_only = [
        ffmpeg, "-y", "-i", str(intro), "-i", str(body),
        "-filter_complex", "[0:v]fps=30,format=yuv420p[v0];[1:v]fps=30,format=yuv420p[v1];[v0][v1]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-an",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart", str(dest),
    ]
    proc = subprocess.run(video_only, capture_output=True, text=True)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 20_000:
        return True
    dest.unlink(missing_ok=True)
    log.info("Concat failed: %s", (proc.stderr or "")[-300:])
    return False


def _ensure_logo(ffmpeg: str, font: str | None) -> Path | None:
    if LOGO_PATH.is_file() and LOGO_PATH.stat().st_size > 2000:
        return LOGO_PATH
    LOGO_PATH.parent.mkdir(parents=True, exist_ok=True)
    font_part = f"fontfile='{_ffmpeg_path(font)}':" if font else ""
    vf = (
        "format=rgba,"
        f"drawtext={font_part}text='P':fontsize=420:fontcolor=0xD4A056:x=(w-text_w)/2:y=(h-text_h)/2-70,"
        f"drawtext={font_part}text='PUZMANIA':fontsize=64:fontcolor=0xF3EEE4:x=(w-text_w)/2:y=h*0.72"
    )
    cmd = [
        ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=0x0E0D0B:s=800x800:d=1",
        "-vf", vf, "-frames:v", "1", str(LOGO_PATH),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and LOGO_PATH.is_file() and LOGO_PATH.stat().st_size > 1000:
        return LOGO_PATH
    log.info("Logo render failed: %s", (proc.stderr or "")[-200:])
    return None


def _drawtext(text: str, font: str | None, size: int, y: str, color: str, *, border: int = 3) -> str:
    if not text:
        return ""
    font_part = f"fontfile='{_ffmpeg_path(font)}':" if font else ""
    return (
        "drawtext="
        f"{font_part}"
        f"text='{_ffmpeg_text(text)}':"
        "x=(w-text_w)/2:"
        f"y={y}:"
        f"fontsize={size}:"
        f"fontcolor={color}:"
        f"borderw={max(0, int(border))}:"
        "bordercolor=0x0E0D0B"
    )


def _bold_font_file() -> str | None:
    candidates = [
        Path(r"C:\Windows\Fonts\ariblk.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _font_file() -> str | None:
    candidates = [
        Path(r"C:\Windows\Fonts\georgia.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Georgia.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _ffmpeg_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def _ffmpeg_text(text: str) -> str:
    cleaned = re.sub(r"[^\w\s.,'!?:/@#-]", "", text)
    return cleaned.replace("\\", "/").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
