import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting, Episode, PLATFORMS, PublishEvent
from app.schemas import EpisodeOut, HistoryOut, PlatformStatus, SettingsOut


def _json_list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def episode_out(ep: Episode) -> EpisodeOut:
    error = ep.last_error
    return EpisodeOut(
        id=ep.episode_id,
        title=ep.title,
        description=ep.description,
        tags=_json_list(ep.tags_hashtags),
        filename=ep.filename,
        duration=ep.duration,
        scheduled=ep.scheduled_date,
        youtube=PlatformStatus(status=ep.youtube_status, id=ep.youtube_video_id, error=error if ep.youtube_status == "failed" else None),
        tiktok=PlatformStatus(status=ep.tiktok_status, id=ep.tiktok_post_id, error=error if ep.tiktok_status == "failed" else None),
        instagram=PlatformStatus(status=ep.instagram_status, id=ep.instagram_media_id, error=error if ep.instagram_status == "failed" else None),
        publishedAt=ep.published_at,
        retryCount=ep.retry_count,
        lastError=ep.last_error,
        overall=ep.overall_status(),
        campaignId=ep.campaign_id,
    )


def history_out(row: PublishEvent) -> HistoryOut:
    return HistoryOut(
        at=row.created_at,
        episode=row.episode_id,
        platform=row.platform,
        result=row.result,
        detail=row.detail,
    )


def default_settings_dict(cfg: Settings) -> dict:
    return {
        "publishTime": cfg.publish_time,
        "timezone": cfg.timezone,
        "episodesPerRun": cfg.episodes_per_run,
        "retryAttempts": cfg.retry_attempts,
        "retryBackoff": cfg.retry_backoff,
        "notifyEmail": cfg.notify_email,
        "notifySlack": cfg.notify_slack,
        "alertOnFailure": cfg.alert_on_failure,
        "dryRun": cfg.dry_run,
        "winnerMode": cfg.winner_mode,
        "pointsMultiplier": cfg.points_multiplier,
        "winnerIntroDays": cfg.winner_intro_days,
        "platforms": {
            "youtube": {"enabled": cfg.youtube_enabled, "connected": cfg.youtube_connected()},
            "tiktok": {"enabled": cfg.tiktok_enabled, "connected": cfg.tiktok_connected()},
            "instagram": {"enabled": cfg.instagram_enabled, "connected": cfg.instagram_connected()},
        },
    }


def load_settings(db: Session, cfg: Settings) -> dict:
    stored = {row.key: row.value for row in db.query(AppSetting).all()}
    data = default_settings_dict(cfg)
    if stored.get("json"):
        try:
            merged = json.loads(stored["json"])
            data.update({k: v for k, v in merged.items() if k != "platforms"})
            if "platforms" in merged:
                for key, value in merged["platforms"].items():
                    data["platforms"].setdefault(key, {}).update(value)
        except json.JSONDecodeError:
            pass
    data["platforms"]["youtube"]["connected"] = cfg.youtube_connected()
    data["platforms"]["tiktok"]["connected"] = cfg.tiktok_connected()
    data["platforms"]["instagram"]["connected"] = cfg.instagram_connected()
    return data


def save_settings(db: Session, cfg: Settings, incoming: dict) -> dict:
    current = load_settings(db, cfg)
    platforms = current["platforms"]
    if "platforms" in incoming and incoming["platforms"]:
        for key, value in incoming["platforms"].items():
            platforms.setdefault(key, {}).update(value)
    incoming = {k: v for k, v in incoming.items() if v is not None and k != "platforms"}
    current.update(incoming)
    current["platforms"] = platforms
    mode = str(current.get("winnerMode") or "seed").strip().lower()
    current["winnerMode"] = mode if mode in {"seed", "clicks"} else "seed"
    try:
        current["pointsMultiplier"] = max(1, min(int(current.get("pointsMultiplier") or 2500), 100_000))
    except (TypeError, ValueError):
        current["pointsMultiplier"] = 2500
    try:
        current["winnerIntroDays"] = max(1, min(int(current.get("winnerIntroDays") or 3), 30))
    except (TypeError, ValueError):
        current["winnerIntroDays"] = 3
    row = db.get(AppSetting, "json")
    payload = json.dumps(current)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key="json", value=payload))
    db.flush()
    return load_settings(db, cfg)


def settings_out(data: dict) -> SettingsOut:
    return SettingsOut.model_validate(data)


def next_run_at(data: dict) -> datetime:
    tz = ZoneInfo(data.get("timezone") or "UTC")
    hour, minute = (data.get("publishTime") or "09:00").split(":")
    now = datetime.now(tz)
    nxt = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt


def queue_ids(episodes: list[Episode]) -> list[str]:
    return [ep.episode_id for ep in episodes if ep.overall_status() in {"queued", "attention"}]


def enabled_platforms(settings: dict) -> list[str]:
    return [name for name in PLATFORMS if settings.get("platforms", {}).get(name, {}).get("enabled", True)]
