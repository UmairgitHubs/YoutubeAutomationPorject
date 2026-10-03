from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import safe_flush
from app.models import Campaign, Episode, PLATFORMS, PublishEvent
from app.publishers import PUBLISHERS
from app.publishers.base import ProgressFn, emit_progress
from app.services import clicks as click_service
from app.services import overlay as overlay_service
from app.services import winners as winner_service
from app.services import queue as queue_service
from app.services.captions import apply_captions, generate_captions
from app.services.notifications import send_summary
from app.services.serialize import enabled_platforms, episode_out, load_settings

log = logging.getLogger("puzmania.orchestrator")


def _backoff_seconds(attempt: int, policy: str) -> float:
    if policy == "fixed":
        return 2.0
    if policy == "linear":
        return 2.0 * attempt
    return min(2.0 ** attempt, 30.0)


def _record(db: Session, episode_id: str, platform: str, result: str, detail: str) -> None:
    db.add(PublishEvent(episode_id=episode_id, platform=platform, result=result, detail=detail))


def _snapshot(episode: Episode) -> SimpleNamespace:
    return SimpleNamespace(
        episode_id=episode.episode_id,
        title=episode.title,
        description=episode.description,
        tags_hashtags=episode.tags_hashtags,
        filename=episode.filename,
        folder_path=episode.folder_path,
        thumbnail_path=episode.thumbnail_path,
    )


def publish_episode(
    db: Session,
    settings: Settings,
    episode_id: str,
    on_progress: ProgressFn | None = None,
) -> dict:
    prefs = load_settings(db, settings)
    dry_run = bool(prefs.get("dryRun", True))
    attempts = int(prefs.get("retryAttempts") or 3)
    policy = str(prefs.get("retryBackoff") or "exponential")
    armed = enabled_platforms(prefs)
    today = datetime.now(ZoneInfo(prefs.get("timezone") or settings.timezone)).date()

    episode = db.get(Episode, episode_id)
    if not episode:
        raise ValueError(f"Unknown episode {episode_id}")

    if episode.campaign_id:
        campaign = db.get(Campaign, episode.campaign_id)
        if campaign:
            try:
                allowed = set(json.loads(campaign.platforms))
            except json.JSONDecodeError:
                allowed = set(PLATFORMS)
            armed = [name for name in armed if name in allowed]

    snap = _snapshot(episode)
    slot_date = episode.scheduled_date or today
    needs_publish = any(
        episode.status_for(platform) in {"pending", "failed"} and platform in armed
        for platform in PLATFORMS
    )
    winner = (
        winner_service.ensure_featured_winner(db, prefs, slot_date, episode.episode_id, settings)
        if needs_publish
        else None
    )
    if winner:
        emit_progress(on_progress, "caption", "Writing the winner’s name onto the intro clip…", 8)
    else:
        emit_progress(on_progress, "caption", "Preparing the episode video…", 8)
    rendered = overlay_service.prepare_publish_video(episode, settings, winner, slot_date) if needs_publish else None
    if rendered is not None:
        snap.folder_path = str(rendered.parent)
        snap.filename = rendered.name
    statuses = {platform: episode.status_for(platform) for platform in PLATFORMS}
    post_ids = {platform: episode.post_id_for(platform) for platform in PLATFORMS}
    retry_count = episode.retry_count or 0
    last_error = episode.last_error
    events: list[tuple[str, str, str]] = [
        ("system", "info", f"{'Dry-run' if dry_run else 'Live'} publish started."),
    ]
    db.commit()

    emit_progress(on_progress, "caption", "Watching the video and writing a PUZMANIA caption…", 20)
    captions = generate_captions(snap, settings)
    site = settings.join_url()
    captions.youtube_description = click_service.stamp_caption_urls(
        captions.youtube_description, site, "youtube", episode.episode_id
    )
    captions.instagram_caption = click_service.stamp_caption_urls(
        captions.instagram_caption, site, "instagram", episode.episode_id
    )
    captions.tiktok_caption = click_service.stamp_caption_urls(
        captions.tiktok_caption, site, "tiktok", episode.episode_id
    )
    apply_captions(snap, captions)
    if captions.source == "openai":
        emit_progress(on_progress, "caption", "ChatGPT captions ready from the video.", 100, "success")
    else:
        emit_progress(
            on_progress,
            "caption",
            captions.error or "ChatGPT unavailable — using episode copy.",
            100,
            "error",
        )
    events.append(("system", "info", f"Captions · {captions.source} · {captions.youtube_title}"))

    for platform in PLATFORMS:
        if platform not in armed:
            if statuses[platform] == "pending":
                statuses[platform] = "skipped"
                events.append((platform, "info", "Skipped — platform disabled in settings."))
            emit_progress(on_progress, platform, f"Skipped — {platform} is turned off on the Platforms page.", 100, "skipped")
            continue
        if statuses[platform] == "success":
            events.append((platform, "info", "Already published — idempotent skip."))
            emit_progress(on_progress, platform, "Already published — skipped.", 100, "success")
            continue
        if statuses[platform] == "skipped":
            emit_progress(on_progress, platform, "Skipped.", 100, "skipped")
            continue

        publisher = PUBLISHERS[platform]
        succeeded = False
        emit_progress(on_progress, platform, f"Starting {platform.title()}…", 1)
        for attempt in range(1, attempts + 1):
            result = publisher.publish(snap, settings, dry_run=dry_run, on_progress=on_progress)
            if result.success:
                statuses[platform] = "success"
                post_ids[platform] = result.post_id
                last_error = None
                label = "dry-run" if result.dry_run else "published"
                events.append((platform, "success", f"{label} · {result.post_id}"))
                emit_progress(
                    on_progress,
                    platform,
                    f"{'Dry-run' if result.dry_run else 'Posted'} · {result.post_id}",
                    100,
                    "success",
                )
                succeeded = True
                break
            retry_count += 1
            last_error = f"{platform}: {result.error or 'Unknown publisher error'}"
            events.append(
                (platform, "failed", f"Attempt {attempt}/{attempts}: {result.error or 'Unknown publisher error'}")
            )
            emit_progress(
                on_progress,
                platform,
                f"Attempt {attempt}/{attempts} failed: {result.error or 'Unknown publisher error'}",
                None,
                "error" if attempt >= attempts else "running",
            )
            if attempt < attempts:
                time.sleep(_backoff_seconds(attempt, policy))
        if not succeeded:
            statuses[platform] = "failed"
            emit_progress(on_progress, platform, last_error or "Publish failed.", None, "error")

    episode = db.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"Unknown episode {episode_id}")
    for platform in PLATFORMS:
        episode.set_status(platform, statuses[platform], post_ids[platform])
    episode.retry_count = retry_count
    episode.last_error = last_error
    if episode.overall_status() == "published":
        episode.published_at = datetime.now(UTC)
    for platform, result, detail in events:
        _record(db, episode_id, platform, result, detail)
    safe_flush(db)

    out = episode_out(episode)
    summary = _summarize(out, dry_run)
    send_summary(
        settings,
        prefs,
        subject=f"Puzmania · {episode.episode_id} · {out.overall}",
        body=summary,
        urgent=out.overall == "attention",
    )
    return {
        "episode_id": episode.episode_id,
        "dry_run": dry_run,
        "results": {
            "youtube": out.youtube.model_dump(),
            "tiktok": out.tiktok.model_dump(),
            "instagram": out.instagram.model_dump(),
        },
        "captions": {
            "source": captions.source,
            "youtubeTitle": captions.youtube_title,
            "instagram": captions.instagram_caption,
            "tiktok": captions.tiktok_caption,
        },
        "overall": out.overall,
        "summary": summary,
    }


def run_daily(db: Session, settings: Settings) -> dict:
    prefs = load_settings(db, settings)
    limit = int(prefs.get("episodesPerRun") or 1)
    today = datetime.now(ZoneInfo(prefs.get("timezone") or settings.timezone)).date()
    targets = queue_service.next_unpublished(db, limit=limit, today=today)
    if not targets:
        _record(db, "-", "system", "info", "Daily run found no unpublished episodes.")
        send_summary(settings, prefs, "Puzmania · idle", "No unpublished episodes in the queue.")
        return {"ran": [], "message": "Queue is empty."}

    results = [publish_episode(db, settings, ep.episode_id) for ep in targets]
    return {"ran": results, "message": f"Published {len(results)} episode(s)."}


def preview(db: Session, settings: Settings) -> dict:
    prefs = load_settings(db, settings)
    limit = int(prefs.get("episodesPerRun") or 1)
    today = datetime.now(ZoneInfo(prefs.get("timezone") or settings.timezone)).date()
    targets = queue_service.next_unpublished(db, limit=limit, today=today)
    return {
        "dryRun": bool(prefs.get("dryRun", True)),
        "publishTime": prefs.get("publishTime"),
        "platforms": enabled_platforms(prefs),
        "episodes": [episode_out(ep).model_dump() for ep in targets],
    }


def _summarize(out, dry_run: bool) -> str:
    mode = "dry-run" if dry_run else "live"
    lines = [f"{out.id} · {out.title} · {mode} · {out.overall}"]
    for name in PLATFORMS:
        row = getattr(out, name)
        extra = row.id or row.error or ""
        lines.append(f"  {name}: {row.status} {extra}".rstrip())
    return "\n".join(lines)
