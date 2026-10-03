from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings, _clean_secret
from app.services.ffmpeg import ffmpeg_exe, ffprobe_exe

log = logging.getLogger("puzmania.captions")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
CAPTION_EXAMPLE = """🧩👀 *Welcome to PUZMANIA!*

Just watch the *JIG PUZZLE* and try to solve it to be the winner! 🏆 Can you figure it out before time runs out?

Solve the puzzle and you could see your name on the *WINNERS LIST!* 🎉🏆

Don’t forget to follow us.
*Wait for the details!* 👀✨

🎬 Series: Jig Puzzle
📺 Episode: 01
🎨 Channel: PUZMANIA

👍 Like the video
💬 Comment your answer
🔔 Follow us for more puzzles and challenges!

#Shorts #PUZMANIA #JigPuzzle #Puzzle #PuzzleChallenge #BrainTeaser #GuessTheAnswer #JigsawPuzzle #Cartoon #Animation #Kids #FamilyFriendly #PuzzleShorts #Episode1"""

SYSTEM_PROMPT = f"""You write PUZMANIA YouTube Shorts / Instagram Reels captions.
Match this voice, emoji, and structure closely:

{CAPTION_EXAMPLE}

Return JSON only with keys: youtube_title, youtube_description, instagram_caption, tiktok_caption.

Rules:
- Look at the attached video frames. Describe the actual puzzle type, characters, colors, and setting you see. Do not invent a solution or spoil the answer.
- Swap JIG PUZZLE / Jig Puzzle for the real series of this episode (Jig Puzzle, Alien Monkeys, or Blur).
- Use the real episode number from the metadata.
- youtube_title: max 100 characters, like "JIG PUZZLE #01 | Can you solve it? | PUZMANIA"
- youtube_description and instagram_caption: the full PUZMANIA caption (same body). Instagram max 2200 characters.
- tiktok_caption: first hook plus 3 hashtags, max 150 characters.
- Always include #Shorts #PUZMANIA and an #EpisodeN hashtag.
Always include the membership URL from the user message (join for the competition with username and age only). Clicking that link from a Short is how someone enters the weekly winner list. Never invent download counts, rankings, or winner names unless a winner line is provided.
"""

SERIES_META = {
    "JIG": {
        "name": "Jig Puzzle",
        "hook": "JIG PUZZLE",
        "hashtags": [
            "JigPuzzle",
            "JigsawPuzzle",
            "PuzzleChallenge",
            "GuessTheAnswer",
            "PuzzleShorts",
        ],
    },
    "ALIEN": {
        "name": "Alien Monkeys",
        "hook": "ALIEN MONKEYS",
        "hashtags": [
            "AlienMonkeys",
            "AlienPuzzle",
            "Cartoon",
            "Animation",
            "PuzzleChallenge",
        ],
    },
    "BLUR": {
        "name": "Blur",
        "hook": "BLUR PUZZLE",
        "hashtags": [
            "BlurPuzzle",
            "GuessThePicture",
            "BrainTeaser",
            "PuzzleChallenge",
            "PuzzleShorts",
        ],
    },
}


@dataclass
class Captions:
    youtube_title: str
    youtube_description: str
    instagram_caption: str
    tiktok_caption: str
    source: str = "fallback"
    error: str | None = None


def generate_captions(episode, settings: Settings) -> Captions:
    site = settings.join_url()
    fallback = fallback_captions(episode, site_url=site)
    key = _clean_secret(settings.openai_api_key)
    if not key:
        log.info("No OpenAI key — using episode copy for captions")
        fallback.error = "OPENAI_API_KEY is missing in .env"
        return fallback

    frames = _preview_frames(_video_path(episode))
    payload = {
        "model": (settings.openai_model or "gpt-4o-mini").strip(),
        "temperature": 0.6,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(episode, frames, site)},
        ],
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
        try:
            data = response.json()
        except ValueError:
            data = {"message": response.text[:300]}
        if response.status_code >= 400:
            detail = _public_openai_error(_openai_error(data, response.text))
            log.warning("OpenAI caption request failed: %s", detail)
            fallback.error = detail
            return fallback
        content = (
            (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
            if isinstance(data, dict)
            else None
        )
        parsed = _parse_json(content or "")
        if not parsed:
            log.warning("OpenAI caption response was not valid JSON")
            fallback.error = "ChatGPT returned a caption that was not valid JSON"
            return fallback
        result = _from_payload(parsed, fallback)
        if frames:
            log.info("Captions generated from %s video frame(s)", len(frames))
        return result
    except httpx.HTTPError as exc:
        log.warning("OpenAI caption request failed: %s", exc)
        fallback.error = f"Could not reach OpenAI: {exc}"
        return fallback


def fallback_captions(episode, site_url: str = "") -> Captions:
    info = series_info(getattr(episode, "episode_id", "") or "")
    body = _template_caption(info, site_url=site_url)
    title = f"{info['hook']} #{info['episode']} | Can you solve it? | PUZMANIA"
    tt = f"🧩 Welcome to PUZMANIA! Watch the *{info['hook']}* #{info['episode']} 🏆 Join {site_url or 'our site'} #Shorts #PUZMANIA #{info['hashtags'][0]}"
    return Captions(
        youtube_title=title[:100],
        youtube_description=body,
        instagram_caption=body[:2200],
        tiktok_caption=tt[:150],
        source="fallback",
    )


def apply_captions(episode, captions: Captions) -> None:
    episode.youtube_title = captions.youtube_title
    episode.youtube_description = captions.youtube_description
    episode.instagram_caption = captions.instagram_caption
    episode.tiktok_caption = captions.tiktok_caption


def series_info(episode_id: str) -> dict:
    raw = (episode_id or "").strip().upper()
    prefix, _, rest = raw.partition("_")
    meta = SERIES_META.get(prefix, {
        "name": "Puzzle",
        "hook": "PUZZLE",
        "hashtags": ["Puzzle", "PuzzleChallenge", "BrainTeaser", "Cartoon", "PuzzleShorts"],
    })
    digits = re.sub(r"\D", "", rest) or re.sub(r"\D", "", raw) or "1"
    number = f"{int(digits):02d}"
    return {
        "id": raw or "PUZ",
        "prefix": prefix or "PUZ",
        "name": meta["name"],
        "hook": meta["hook"],
        "episode": number,
        "hashtags": list(meta["hashtags"]),
    }


def _from_payload(data: dict, fallback: Captions) -> Captions:
    title = str(data.get("youtube_title") or fallback.youtube_title).strip()[:100]
    yt_desc = str(data.get("youtube_description") or fallback.youtube_description).strip()[:4900]
    ig = str(data.get("instagram_caption") or fallback.instagram_caption).strip()[:2200]
    tt = str(data.get("tiktok_caption") or fallback.tiktok_caption).strip()[:150]
    if not title or not yt_desc or not ig:
        return fallback
    return Captions(
        youtube_title=title,
        youtube_description=yt_desc,
        instagram_caption=ig,
        tiktok_caption=tt or title[:150],
        source="openai",
    )


def _user_content(episode, frames: list[bytes], site_url: str = "") -> list[dict]:
    info = series_info(getattr(episode, "episode_id", "") or "")
    tags = ", ".join(_tags(episode)) or "puzmania, shorts, puzzle"
    text = (
        f"Episode id: {info['id']}\n"
        f"Series: {info['name']}\n"
        f"Puzzle hook: {info['hook']}\n"
        f"Episode number: {info['episode']}\n"
        f"Title: {getattr(episode, 'title', '')}\n"
        f"Existing description: {getattr(episode, 'description', '')}\n"
        f"Tags: {tags}\n"
        f"Membership URL (always include): {site_url}\n"
        f"Video file: {getattr(episode, 'filename', '')}\n"
        "Frames from the episode video are attached. Analyze them, then write a fresh PUZMANIA caption "
        "in the example structure for THIS episode. Use the real series name and episode number."
    )
    content: list[dict] = [{"type": "text", "text": text}]
    for jpeg in frames:
        b64 = base64.b64encode(jpeg).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            }
        )
    return content


def _template_caption(info: dict, site_url: str = "") -> str:
    extra = " ".join(f"#{tag}" for tag in info["hashtags"])
    join_block = ""
    if site_url:
        join_block = (
            f"Become a member for the competition (username and age only): {site_url}\n"
            "Tap that link from a Short to join — this week’s winner is named on our videos!\n\n"
        )
    return (
        "🧩👀 *Welcome to PUZMANIA!*\n\n"
        f"Just watch the *{info['hook']}* and try to solve it to be the winner! 🏆 "
        "Can you figure it out before time runs out?\n\n"
        "Solve the puzzle and you could see your name on the *WINNERS LIST!* 🎉🏆\n\n"
        f"{join_block}"
        "Don’t forget to follow us.\n"
        "*Wait for the details!* 👀✨\n\n"
        f"🎬 Series: {info['name']}\n"
        f"📺 Episode: {info['episode']}\n"
        "🎨 Channel: PUZMANIA\n\n"
        "👍 Like the video\n"
        "💬 Comment your answer\n"
        "🔔 Follow us for more puzzles and challenges!\n\n"
        f"#Shorts #PUZMANIA {extra} #Puzzle #BrainTeaser #Cartoon #Animation #Kids #FamilyFriendly #Episode{int(info['episode'])}"
    )


def _video_path(episode) -> Path | None:
    folder = getattr(episode, "folder_path", None)
    if not folder:
        return None
    root = Path(folder)
    name = getattr(episode, "filename", None)
    if name:
        candidate = root / name
        if candidate.exists():
            return candidate
    for pattern in ("*.mp4", "*.mov", "*.m4v"):
        found = next(root.glob(pattern), None)
        if found:
            return found
    return None


def _preview_frames(video: Path | None, count: int = 3) -> list[bytes]:
    if video is None or not video.exists():
        return []
    ffmpeg = ffmpeg_exe()
    if not ffmpeg:
        log.info("ffmpeg not available — captions will use metadata only")
        return []
    stamps = _frame_stamps(video, count)
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="puz-caption-") as raw:
        tmp = Path(raw)
        for index, stamp in enumerate(stamps, start=1):
            dest = tmp / f"frame-{index:02d}.jpg"
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                f"{stamp:.2f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=512:-2",
                "-q:v",
                "5",
                str(dest),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
                continue
            frames.append(dest.read_bytes())
    if not frames:
        log.info("Could not extract preview frames from %s", video.name)
    return frames


def _frame_stamps(video: Path, count: int) -> list[float]:
    duration = _duration_seconds(video)
    if duration and duration > 1:
        spots = (0.18, 0.48, 0.78)[:count]
        return [max(0.2, min(duration - 0.2, duration * spot)) for spot in spots]
    return [1.0, 3.0, 5.0][:count]


def _duration_seconds(video: Path) -> float | None:
    ffprobe = ffprobe_exe()
    if not ffprobe:
        return None
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        value = float((proc.stdout or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _tags(episode) -> list[str]:
    raw = getattr(episode, "tags_hashtags", None) or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        tags = [re.sub(r"^#", "", str(item).strip()) for item in data if str(item).strip()]
        return [tag for tag in tags if tag][:20]
    except (TypeError, json.JSONDecodeError):
        return ["puzmania", "shorts", "puzzle"]


def _parse_json(raw: str) -> dict | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _openai_error(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if payload.get("message"):
            return str(payload["message"])
    return fallback[:300]


def _public_openai_error(detail: str) -> str:
    lower = detail.lower()
    if "incorrect api key" in lower or "invalid api key" in lower or "invalid_api_key" in lower:
        return "OpenAI rejected the API key. Create a new secret key at platform.openai.com and set OPENAI_API_KEY in .env."
    if "insufficient_quota" in lower or "quota" in lower:
        return "OpenAI quota is exhausted. Add billing credit, then publish again."
    if "model" in lower and ("does not exist" in lower or "not found" in lower):
        return "The OpenAI model in OPENAI_MODEL is not available for this key."
    return (detail or "OpenAI request failed")[:240]
