from __future__ import annotations

import secrets
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import safe_flush
from app.models import ClickEvent, Member

VISITOR_COOKIE = "puz_vid"
PLATFORMS = {"youtube", "instagram", "tiktok", "short", "shorts"}


def new_visitor_token() -> str:
    return secrets.token_hex(16)


def normalize_platform(raw: str | None) -> str:
    name = (raw or "").strip().lower()
    if name in {"yt", "youtube", "youtu.be"}:
        return "youtube"
    if name in {"ig", "instagram", "reel", "reels"}:
        return "instagram"
    if name in {"tt", "tiktok"}:
        return "tiktok"
    if name in {"short", "shorts"}:
        return "short"
    return name if name in PLATFORMS else ""


def tracked_join_url(base: str, platform: str, episode_id: str) -> str:
    raw = (base or "").strip() or "/join"
    parsed = urlparse(raw)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["src"] = [platform]
    query["ep"] = [episode_id]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def stamp_caption_urls(text: str, base: str, platform: str, episode_id: str) -> str:
    body = text or ""
    tracked = tracked_join_url(base, platform, episode_id)
    for candidate in (base, base.rstrip("/"), base + "/"):
        if candidate and candidate in body:
            body = body.replace(candidate, tracked)
            return body
    if tracked not in body and base:
        body = f"{body.rstrip()}\n\nJoin: {tracked}"
    return body


def record_click(
    db: Session,
    *,
    visitor_token: str,
    platform: str,
    episode_id: str,
    member_id: int | None = None,
) -> ClickEvent | None:
    token = (visitor_token or "").strip()
    src = normalize_platform(platform)
    ep = (episode_id or "").strip().upper()[:48]
    if not token or not src:
        return None
    if not ep:
        ep = "SITE"
    existing = db.scalar(
        select(ClickEvent).where(ClickEvent.visitor_token == token, ClickEvent.episode_id == ep)
    )
    if existing:
        if member_id and existing.member_id is None:
            existing.member_id = member_id
            safe_flush(db)
        return existing
    row = ClickEvent(
        visitor_token=token,
        member_id=member_id,
        episode_id=ep,
        platform=src,
    )
    db.add(row)
    safe_flush(db)
    return row


def attach_visitor(db: Session, member: Member, visitor_token: str | None) -> int:
    token = (visitor_token or "").strip()
    if not token:
        return member_click_count(db, member.id)
    member.visitor_token = member.visitor_token or token
    rows = list(db.scalars(select(ClickEvent).where(ClickEvent.visitor_token == token)))
    for row in rows:
        if row.member_id is None:
            row.member_id = member.id
    safe_flush(db)
    return member_click_count(db, member.id)


def member_click_count(db: Session, member_id: int) -> int:
    value = db.scalar(
        select(func.count(func.distinct(ClickEvent.episode_id))).where(ClickEvent.member_id == member_id)
    )
    return int(value or 0)


def member_points(db: Session, member_id: int, multiplier: int) -> int:
    return member_click_count(db, member_id) * max(1, int(multiplier))


def leaderboard(db: Session, multiplier: int, limit: int = 20) -> list[dict]:
    rows = list(
        db.execute(
            select(ClickEvent.member_id, func.count(func.distinct(ClickEvent.episode_id)))
            .where(ClickEvent.member_id.is_not(None))
            .group_by(ClickEvent.member_id)
            .order_by(func.count(func.distinct(ClickEvent.episode_id)).desc())
            .limit(limit)
        )
    )
    out: list[dict] = []
    for member_id, clicks in rows:
        member = db.get(Member, member_id)
        if not member:
            continue
        count = int(clicks or 0)
        out.append(
            {
                "memberId": member.id,
                "username": member.username,
                "clicks": count,
                "points": count * max(1, int(multiplier)),
            }
        )
    return out


def top_click_member(db: Session, multiplier: int) -> dict | None:
    board = leaderboard(db, multiplier, limit=1)
    return board[0] if board else None


def click_stats(db: Session) -> dict:
    total = db.scalar(select(func.count()).select_from(ClickEvent)) or 0
    members = db.scalar(
        select(func.count(func.distinct(ClickEvent.member_id))).where(ClickEvent.member_id.is_not(None))
    ) or 0
    return {"clicks": int(total), "clickMembers": int(members)}


def join_url_for_episode(settings: Settings, platform: str, episode_id: str) -> str:
    return tracked_join_url(settings.join_url(), platform, episode_id)
