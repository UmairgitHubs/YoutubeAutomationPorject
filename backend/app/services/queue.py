from __future__ import annotations

import time
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import safe_flush
from app.models import Campaign, Episode


def listed(db: Session) -> list[Episode]:
    return list(db.scalars(select(Episode).order_by(Episode.queue_order, Episode.episode_id)))


def active_queue(db: Session) -> list[Episode]:
    return [ep for ep in listed(db) if ep.overall_status() in {"queued", "attention"}]


def next_unpublished(db: Session, limit: int = 1, today: date | None = None) -> list[Episode]:
    day = today or date.today()
    grouped: dict[str, list[Episode]] = defaultdict(list)
    legacy: list[Episode] = []
    for ep in active_queue(db):
        if ep.scheduled_date and ep.scheduled_date > day:
            continue
        if ep.campaign_id:
            grouped[ep.campaign_id].append(ep)
        else:
            legacy.append(ep)

    picked: list[Episode] = []
    for campaign_id, items in grouped.items():
        campaign = db.get(Campaign, campaign_id)
        cap = campaign.videos_per_day if campaign else 1
        items.sort(key=lambda row: (row.scheduled_date or day, row.queue_order, row.episode_id))
        picked.extend(items[: max(1, cap)])
    picked.extend(legacy[: max(0, limit)])
    return picked


def reorder(db: Session, episode_id: str, direction: int) -> list[str]:
    queue = active_queue(db)
    ids = [ep.episode_id for ep in queue]
    try:
        index = ids.index(episode_id)
    except ValueError as exc:
        raise ValueError(f"{episode_id} is not in the queue") from exc
    target = index + int(direction)
    if target < 0 or target >= len(queue):
        return ids
    queue[index], queue[target] = queue[target], queue[index]
    for order, ep in enumerate(queue, start=1):
        ep.queue_order = order
    _flush(db)
    return [ep.episode_id for ep in queue]


def reset_order(db: Session) -> list[str]:
    queue = active_queue(db)
    queue.sort(key=lambda ep: (ep.scheduled_date or ep.created_at, ep.episode_id))
    for order, ep in enumerate(queue, start=1):
        ep.queue_order = order
    _flush(db)
    return [ep.episode_id for ep in queue]


def _flush(db: Session) -> None:
    safe_flush(db)


def skip(db: Session, episode_id: str) -> Episode:
    last: OperationalError | None = None
    for attempt in range(8):
        try:
            ep = db.get(Episode, episode_id)
            if not ep:
                raise ValueError(f"Unknown episode {episode_id}")
            for platform in ("youtube", "tiktok", "instagram"):
                if ep.status_for(platform) == "pending":
                    ep.set_status(platform, "skipped")
            _flush(db)
            return ep
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            db.rollback()
            time.sleep(0.25 * (attempt + 1))
    raise last or ValueError(f"Could not skip {episode_id}")


def retry_failed(db: Session, episode_id: str) -> Episode:
    ep = db.get(Episode, episode_id)
    if not ep:
        raise ValueError(f"Unknown episode {episode_id}")
    for platform in ("youtube", "tiktok", "instagram"):
        if ep.status_for(platform) == "failed":
            ep.set_status(platform, "pending")
    ep.retry_count = 0
    ep.last_error = None
    if ep.queue_order == 0:
        ep.queue_order = 1
    _flush(db)
    return ep
