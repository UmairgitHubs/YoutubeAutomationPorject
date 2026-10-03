from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import safe_flush
from app.models import FeaturedWinner, Member
from app.services import clicks as click_service

log = logging.getLogger("puzmania.winners")

SEED_USERNAMES = [
    "luna_solve",
    "pixelquest",
    "jigmaster",
    "blur_kid",
    "monkeyfan",
    "puzzlebee",
    "nova_play",
    "riddlefox",
    "goldentile",
    "map_hunter",
    "quietqueen",
    "brainyben",
    "solveitnow",
    "aliencrew",
    "tile_wizard",
    "starjigsaw",
    "fastthinker",
    "puzzninja",
    "orbitkid",
    "cluefinder",
    "mintpuzzle",
    "shadowgrid",
    "brightbyte",
    "quiznova",
    "tapmaster",
]


def winner_line(username: str, points: int) -> str:
    name = (username or "a member").strip()
    return f"This weeks winner is {name} with {int(points)}"


def winner_prefs(prefs: dict | None, settings=None) -> dict:
    data = prefs or {}
    mode = str(data.get("winnerMode") or getattr(settings, "winner_mode", "seed") or "seed").strip().lower()
    if mode not in {"seed", "clicks"}:
        mode = "seed"
    try:
        multiplier = int(data.get("pointsMultiplier") or getattr(settings, "points_multiplier", 2500) or 2500)
    except (TypeError, ValueError):
        multiplier = 2500
    try:
        interval = int(data.get("winnerIntroDays") or getattr(settings, "winner_intro_days", 3) or 3)
    except (TypeError, ValueError):
        interval = 3
    return {
        "mode": mode,
        "multiplier": max(1, min(multiplier, 100_000)),
        "interval": max(1, min(interval, 30)),
        "points_min": int(getattr(settings, "winner_points_min", 2000) or 2000),
        "points_max": int(getattr(settings, "winner_points_max", 5000) or 5000),
    }


def last_featured(db: Session, before: date | None = None) -> FeaturedWinner | None:
    stmt = select(FeaturedWinner).order_by(FeaturedWinner.show_date.desc())
    if before is not None:
        stmt = stmt.where(FeaturedWinner.show_date <= before)
    return db.scalars(stmt.limit(1)).first()


def featured_for_date(db: Session, show_date: date) -> FeaturedWinner | None:
    return db.scalar(select(FeaturedWinner).where(FeaturedWinner.show_date == show_date))


def is_intro_day(db: Session, slot_date: date, interval: int = 3) -> bool:
    previous = db.scalar(
        select(FeaturedWinner)
        .where(FeaturedWinner.show_date < slot_date)
        .order_by(FeaturedWinner.show_date.desc())
        .limit(1)
    )
    if previous is None:
        return True
    return (slot_date - previous.show_date).days >= max(1, interval)


def next_intro_date(db: Session, today: date, interval: int = 3) -> date:
    if is_intro_day(db, today, interval):
        return today
    previous = last_featured(db, before=today)
    if previous is None:
        return today
    return previous.show_date + timedelta(days=max(1, interval))


def pick_seed_winner(rng: random.Random | None = None, *, exclude: str | None = None, points_min: int = 2000, points_max: int = 5000) -> tuple[str, int]:
    dice = rng or random.Random()
    names = [name for name in SEED_USERNAMES if name.lower() != (exclude or "").lower()] or list(SEED_USERNAMES)
    return dice.choice(names), dice.randint(points_min, points_max)


def ensure_featured_winner(
    db: Session,
    prefs: dict,
    slot_date: date,
    episode_id: str | None = None,
    settings=None,
) -> FeaturedWinner | None:
    """Create or reuse the winner card for this publish day. None when this day has no intro."""
    cfg = winner_prefs(prefs, settings)
    existing = featured_for_date(db, slot_date)
    if existing:
        if episode_id and not existing.episode_id:
            existing.episode_id = episode_id
            safe_flush(db)
        return existing
    if not is_intro_day(db, slot_date, cfg["interval"]):
        return None
    return create_featured_winner(db, prefs, slot_date, episode_id=episode_id, settings=settings)


def create_featured_winner(
    db: Session,
    prefs: dict,
    show_date: date,
    *,
    episode_id: str | None = None,
    username: str | None = None,
    points: int | None = None,
    member_id: int | None = None,
    source: str | None = None,
    settings=None,
) -> FeaturedWinner:
    cfg = winner_prefs(prefs, settings)
    previous = last_featured(db, before=show_date - timedelta(days=1)) if show_date else last_featured(db)
    chosen_source = source or cfg["mode"]
    chosen_name = (username or "").strip()
    chosen_points = points
    chosen_member = member_id

    if chosen_name:
        chosen_source = source or "manual"
        if chosen_points is None and chosen_member:
            chosen_points = click_service.member_points(db, chosen_member, cfg["multiplier"])
        if chosen_points is None:
            chosen_points = cfg["points_min"]
    elif cfg["mode"] == "clicks" or chosen_source == "clicks":
        top = click_service.top_click_member(db, cfg["multiplier"])
        if top:
            chosen_name = top["username"]
            chosen_points = top["points"]
            chosen_member = top["memberId"]
            chosen_source = "clicks"
        else:
            log.info("No click-through members yet — using a seed username for %s", show_date)
            chosen_name, chosen_points = pick_seed_winner(
                exclude=previous.username if previous else None,
                points_min=cfg["points_min"],
                points_max=cfg["points_max"],
            )
            chosen_source = "seed"
    else:
        chosen_name, chosen_points = pick_seed_winner(
            exclude=previous.username if previous else None,
            points_min=cfg["points_min"],
            points_max=cfg["points_max"],
        )
        chosen_source = "seed"

    row = FeaturedWinner(
        show_date=show_date,
        username=chosen_name[:32],
        points=int(chosen_points),
        source=chosen_source,
        member_id=chosen_member,
        episode_id=episode_id,
    )
    db.add(row)
    safe_flush(db)
    return row


def featured_out(row: FeaturedWinner | None) -> dict | None:
    if not row:
        return None
    return {
        "date": row.show_date.isoformat(),
        "username": row.username,
        "points": row.points,
        "source": row.source,
        "memberId": row.member_id,
        "episodeId": row.episode_id,
        "line": winner_line(row.username, row.points),
    }


def featured_list(db: Session, limit: int = 20) -> list[dict]:
    rows = list(db.scalars(select(FeaturedWinner).order_by(FeaturedWinner.show_date.desc()).limit(limit)))
    return [featured_out(row) for row in rows if row]


def member_public(db: Session, member: Member, multiplier: int) -> dict:
    clicks = click_service.member_click_count(db, member.id)
    return {
        "id": member.id,
        "username": member.username,
        "age": member.age,
        "country": member.country,
        "region": member.region,
        "clicks": clicks,
        "points": clicks * max(1, int(multiplier)),
        "channelPoints": int(member.channel_points or 0),
    }
