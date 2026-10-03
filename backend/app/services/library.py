from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import safe_flush
from app.models import Episode, PublishEvent
from app.services import gdrive

log = logging.getLogger("puzmania.library")

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm"}


def refresh_library(db: Session, settings: Settings) -> tuple[list[str], list[str], dict]:
    """Pull new packages from Google Drive (if configured), then scan the local folder."""
    try:
        drive = gdrive.sync_episodes(settings, db=db)
    except Exception as exc:
        log.exception("Google Drive sync failed")
        drive = {"ok": False, "skipped": False, "error": str(exc)[:300]}
    found, removed = scan_episode_folder(db, settings)
    return found, removed, drive


def scan_episode_folder(db: Session, settings: Settings) -> tuple[list[str], list[str]]:
    """Detect new or updated episode packages (video + metadata.json)."""
    root = Path(settings.episodes_dir)
    root.mkdir(parents=True, exist_ok=True)
    seen: list[str] = []
    max_order = db.query(Episode.queue_order).order_by(Episode.queue_order.desc()).first()
    next_order = (max_order[0] + 1) if max_order and max_order[0] is not None else 1

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        meta_path = folder / "metadata.json"
        if not meta_path.exists():
            log.info("Skipping %s — no metadata.json", folder.name)
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            log.warning("Invalid metadata in %s: %s", folder, exc)
            continue

        episode_id = str(meta.get("id") or folder.name).upper()
        video = _find_video(folder, meta.get("filename"))
        thumb = _find_thumb(folder, meta.get("thumbnail"))
        seen.append(episode_id)

        existing = db.get(Episode, episode_id)
        if existing:
            _assign(existing, "folder_path", str(folder))
            if video:
                _assign(existing, "filename", video.name)
            if thumb:
                _assign(existing, "thumbnail_path", str(thumb))
            if meta.get("title"):
                _assign(existing, "title", meta["title"])
            if meta.get("description") is not None:
                _assign(existing, "description", meta["description"])
            if meta.get("tags"):
                _assign(existing, "tags_hashtags", json.dumps(meta["tags"]))
            if meta.get("duration"):
                _assign(existing, "duration", meta["duration"])
            if meta.get("scheduled"):
                parsed = _parse_date(meta["scheduled"])
                if parsed is not None:
                    _assign(existing, "scheduled_date", parsed)
            continue

        db.add(
            Episode(
                episode_id=episode_id,
                filename=video.name if video else meta.get("filename") or "",
                title=meta.get("title") or episode_id,
                description=meta.get("description") or "",
                tags_hashtags=json.dumps(meta.get("tags") or []),
                thumbnail_path=str(thumb) if thumb else None,
                folder_path=str(folder),
                duration=meta.get("duration"),
                scheduled_date=_parse_date(meta.get("scheduled")),
                queue_order=next_order,
            )
        )
        next_order += 1
        log.info("Queued new episode %s from %s", episode_id, folder)

    removed = _prune_missing(db, set(seen))
    safe_flush(db)
    return seen, removed


def _prune_missing(db: Session, keep_ids: set[str]) -> list[str]:
    """Drop library rows whose package is no longer on disk."""
    if not keep_ids:
        return []
    removed: list[str] = []
    for ep in list(db.query(Episode).all()):
        if ep.episode_id in keep_ids:
            continue
        removed.append(ep.episode_id)
        db.query(PublishEvent).filter(PublishEvent.episode_id == ep.episode_id).delete()
        db.delete(ep)
        log.info("Removed %s — folder/video is missing", ep.episode_id)
    return removed


def _assign(episode: Episode, field: str, value) -> None:
    if getattr(episode, field) != value:
        setattr(episode, field, value)


def _find_video(folder: Path, filename: str | None) -> Path | None:
    if filename:
        candidate = folder / filename
        if candidate.exists():
            return candidate
    videos = [p for p in folder.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES]
    return videos[0] if videos else None


def _find_thumb(folder: Path, filename: str | None) -> Path | None:
    if filename and (folder / filename).exists():
        return folder / filename
    for name in ("thumbnail.jpg", "thumbnail.png", "thumb.jpg"):
        if (folder / name).exists():
            return folder / name
    return None


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
