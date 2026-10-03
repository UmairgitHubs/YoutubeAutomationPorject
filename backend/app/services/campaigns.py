from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import shutil
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import safe_flush
from app.models import PLATFORMS, Campaign, Episode

log = logging.getLogger("puzmania.campaigns")

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}
PLATFORM_PRESETS = {
    "youtube": ["youtube"],
    "instagram": ["instagram"],
    "youtube_instagram": ["youtube", "instagram"],
    "all": ["youtube", "tiktok", "instagram"],
}


def parse_platforms(raw: str | list[str] | None) -> list[str]:
    if isinstance(raw, list):
        tokens = [str(item).strip().lower() for item in raw]
    else:
        text = (raw or "youtube_instagram").strip().lower().replace("-", "_")
        if text in PLATFORM_PRESETS:
            return list(PLATFORM_PRESETS[text])
        tokens = [part.strip() for part in re.split(r"[,\s]+", text) if part.strip()]
    chosen = [name for name in PLATFORMS if name in tokens]
    if not chosen:
        raise ValueError("Choose at least one platform: YouTube, Instagram, or TikTok.")
    return chosen


def expand_schedule(
    start: date,
    end: date,
    videos_per_day: int,
    video_count: int,
    loop: bool,
) -> list[tuple[date, int]]:
    """Return (publish_date, source_video_index) slots."""
    if end < start:
        raise ValueError("End date must be on or after the start date.")
    if videos_per_day < 1:
        raise ValueError("Videos per day must be at least 1.")
    if video_count < 1:
        raise ValueError("Select at least one video.")
    slots: list[tuple[date, int]] = []
    days = (end - start).days + 1
    for day_offset in range(days):
        day = start + timedelta(days=day_offset)
        for n in range(videos_per_day):
            index = day_offset * videos_per_day + n
            if not loop and index >= video_count:
                return slots
            slots.append((day, index % video_count))
    if not slots:
        raise ValueError("That date range does not create any publishing slots.")
    return slots


def parse_caption_file(text: str, filenames: list[str]) -> list[dict[str, str]]:
    """Align titles/descriptions to the uploaded videos, in order."""
    blank = [{"title": "", "description": ""} for _ in filenames]
    raw = (text or "").strip()
    if not raw:
        return blank

    by_name = {Path(name).name.lower(): i for i, name in enumerate(filenames)}
    rows: list[dict[str, str]] = []

    if "," in raw.splitlines()[0]:
        reader = csv.DictReader(io.StringIO(raw))
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        if headers and any(h in headers for h in ("title", "description", "caption", "filename", "file")):
            mapped = list(blank)
            sequential: list[dict[str, str]] = []
            for row in reader:
                lower = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}
                item = {
                    "title": lower.get("title") or "",
                    "description": lower.get("description") or lower.get("caption") or lower.get("text") or "",
                }
                fname = lower.get("filename") or lower.get("file") or lower.get("video") or ""
                if fname and Path(fname).name.lower() in by_name:
                    mapped[by_name[Path(fname).name.lower()]] = item
                else:
                    sequential.append(item)
            if any(item["title"] or item["description"] for item in mapped):
                return mapped
            rows = sequential

    if not rows:
        blocks = [block.strip() for block in re.split(r"\n\s*\n|^\s*---\s*$", raw, flags=re.MULTILINE) if block.strip()]
        if len(blocks) == 1 and "\n" not in raw.strip() and "|" not in raw:
            rows = [{"title": "", "description": line.strip()} for line in raw.splitlines() if line.strip()]
        else:
            for block in blocks:
                line = block.splitlines()[0].strip()
                rest = "\n".join(block.splitlines()[1:]).strip()
                if "|" in line and not rest:
                    title, _, desc = line.partition("|")
                    rows.append({"title": title.strip(), "description": desc.strip()})
                elif rest:
                    rows.append({"title": line, "description": rest})
                else:
                    rows.append({"title": "", "description": line})

    out = list(blank)
    for index, row in enumerate(rows[: len(out)]):
        out[index] = {
            "title": (row.get("title") or "").strip(),
            "description": (row.get("description") or "").strip(),
        }
    return out


def create_campaign(
    db: Session,
    settings: Settings,
    *,
    name: str,
    platforms: list[str],
    start: date,
    end: date,
    videos_per_day: int,
    loop: bool,
    video_paths: list[Path],
    captions: list[dict[str, str]],
) -> Campaign:
    slots = expand_schedule(start, end, videos_per_day, len(video_paths), loop)
    campaign_id = uuid.uuid4().hex[:8].upper()
    label = (name or "").strip() or f"Batch {start.isoformat()}"
    campaign = Campaign(
        id=campaign_id,
        name=label,
        platforms=json.dumps(platforms),
        start_date=start,
        end_date=end,
        videos_per_day=videos_per_day,
        loop=loop,
        status="active",
        video_count=len(video_paths),
        slot_count=len(slots),
    )
    db.add(campaign)
    safe_flush(db)

    source_dir = Path(settings.episodes_dir) / "_sources" / campaign_id
    source_dir.mkdir(parents=True, exist_ok=True)
    stored: list[Path] = []
    for video in video_paths:
        dest = source_dir / _safe_name(video.name)
        if dest.exists():
            dest = source_dir / f"{dest.stem}_{len(stored)+1}{dest.suffix}"
        _place(video, dest)
        stored.append(dest)

    max_order = db.query(Episode.queue_order).order_by(Episode.queue_order.desc()).first()
    next_order = (max_order[0] + 1) if max_order and max_order[0] is not None else 1
    skipped = [name for name in PLATFORMS if name not in platforms]

    for slot_index, (slot_date, video_index) in enumerate(slots, start=1):
        source = stored[video_index]
        caption = captions[video_index] if video_index < len(captions) else {"title": "", "description": ""}
        episode_id = f"{campaign_id}_{slot_date.strftime('%Y%m%d')}_{slot_index:02d}"
        folder = Path(settings.episodes_dir) / episode_id
        folder.mkdir(parents=True, exist_ok=True)
        dest_video = folder / source.name
        _place(source, dest_video)
        title = caption.get("title") or Path(source.stem).name.replace("_", " ").replace("-", " ").title()
        description = caption.get("description") or (
            "A Puzmania short. Tap the link, join with a username, and you could be this week’s winner."
        )
        meta = {
            "id": episode_id,
            "title": title,
            "description": description,
            "tags": ["puzmania", "shorts", "puzzle", "competition"],
            "filename": dest_video.name,
            "scheduled": slot_date.isoformat(),
            "campaign": campaign_id,
        }
        (folder / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        episode = Episode(
            episode_id=episode_id,
            filename=dest_video.name,
            title=title,
            description=description,
            tags_hashtags=json.dumps(meta["tags"]),
            folder_path=str(folder),
            scheduled_date=slot_date,
            queue_order=next_order,
            campaign_id=campaign_id,
        )
        for platform in skipped:
            episode.set_status(platform, "skipped")
        db.add(episode)
        next_order += 1

    safe_flush(db)
    log.info(
        "Campaign %s queued %s slot(s) from %s video(s) %s→%s (%s/day, loop=%s)",
        campaign_id,
        len(slots),
        len(stored),
        start,
        end,
        videos_per_day,
        loop,
    )
    return campaign


def campaign_out(campaign: Campaign, today: date | None = None) -> dict:
    day = today or date.today()
    if campaign.status == "paused":
        state = "paused"
    elif day > campaign.end_date:
        state = "completed"
    elif day < campaign.start_date:
        state = "scheduled"
    else:
        state = "active"
    try:
        platforms = json.loads(campaign.platforms)
    except json.JSONDecodeError:
        platforms = ["youtube", "instagram"]
    return {
        "id": campaign.id,
        "name": campaign.name,
        "platforms": platforms,
        "startDate": campaign.start_date.isoformat(),
        "endDate": campaign.end_date.isoformat(),
        "videosPerDay": campaign.videos_per_day,
        "loop": bool(campaign.loop),
        "status": state,
        "videoCount": campaign.video_count,
        "slotCount": campaign.slot_count,
    }


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip("._ ")
    return cleaned or "video.mp4"


def _place(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)
