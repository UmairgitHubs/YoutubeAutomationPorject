from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, Settings, get_settings
from app.database import safe_flush
from app.models import Episode, Member, Puzzle, PuzzleAttempt
from app.services.join import _normalize
from app.services.series import (
    SERIES_PREFIX,
    answer_key,
    episode_number,
    parse_cycle,
    series_of,
)

log = logging.getLogger("puzmania.channel")

TIMER_START = 100
POINTS_FACTOR = 9
MISS_MESSAGES = [
    "You missed this time",
    "Next time I hope",
    "Try another puzzle",
    "So close — keep going",
    "Not that one, friend",
    "Almost! Pick a new video",
    "Nice try — another puzzle awaits",
    "That was a tricky one",
    "Shake it off and try a new clip",
    "Oops! Your points stay with you",
    "Wrong guess, still a star",
    "Keep smiling and try the next one",
    "Not today — the next puzzle is waiting",
    "Good effort! Choose another video",
    "Missed it — no points lost",
    "Have another go on a new puzzle",
    "That answer slipped away",
    "Brave try! On to the next clip",
    "The picture fooled you this time",
    "One miss, many more videos to play",
    "Don't worry — try a different puzzle",
    "Next video, new chance",
    "You can do the next one",
    "Keep playing, champion",
]


def seed_channel_puzzles(db: Session, episodes_dir: Path | None = None) -> int:
    """Add or refresh one channel puzzle per JIG/ALIEN/BLUR video. Aligns answers to the on-video cycle."""
    root = Path(episodes_dir or PROJECT_ROOT / "episodes")
    added = 0
    updated = 0
    if not root.is_dir():
        return 0
    existing = {
        row.episode_id: row
        for row in db.scalars(select(Puzzle).where(Puzzle.episode_id.is_not(None)))
        if row.episode_id
    }
    cfg = get_settings()
    for folder in sorted(root.iterdir(), key=lambda path: path.name):
        if not folder.is_dir():
            continue
        episode_id = folder.name.upper()
        series = series_of(episode_id)
        if not series:
            continue
        has_package = (folder / "metadata.json").is_file() or any(folder.glob("*.mp4"))
        if not has_package:
            continue
        spec = _spec_for(episode_id, cfg)
        row = existing.get(episode_id)
        if row:
            if row.answer != spec["answer"] or row.choices != json.dumps(spec["choices"]) or row.prompt != spec["prompt"]:
                row.answer = spec["answer"]
                row.choices = json.dumps(spec["choices"])
                row.prompt = spec["prompt"]
                row.series = series
                updated += 1
            continue
        db.add(
            Puzzle(
                prompt=spec["prompt"],
                answer=spec["answer"],
                choices=json.dumps(spec["choices"]),
                active=True,
                episode_id=episode_id,
                series=series,
            )
        )
        added += 1
    if added or updated:
        safe_flush(db)
        log.info("Channel puzzles: %s added, %s answers aligned", added, updated)
    return added


def _series_of(episode_id: str) -> str | None:
    return series_of(episode_id)


def _episode_number(episode_id: str) -> int:
    return episode_number(episode_id)


def _spec_for(episode_id: str, settings: Settings | None = None) -> dict:
    cfg = settings or get_settings()
    series = series_of(episode_id) or "JIG"
    number = episode_number(episode_id)
    fallback = {"JIG": "ABCD", "ALIEN": "123", "BLUR": "ABCD"}[series]
    cycle = parse_cycle(getattr(cfg, f"answer_cycle_{series.lower()}"), fallback)
    offset = int(getattr(cfg, f"answer_offset_{series.lower()}") or 0)
    answer = answer_key(series, number, cycle, offset)
    joined = ", ".join(cycle[:-1]) + (f", or {cycle[-1]}" if len(cycle) > 1 else cycle[0])
    prompt = f"Watch the clip. Tap the correct option from the video ({joined}). ({SERIES_PREFIX[series]} #{number:02d})"
    return {"prompt": prompt, "answer": answer, "choices": list(cycle)}


def video_file(episode_id: str, db: Session | None = None) -> Path | None:
    name = (episode_id or "").strip().upper()
    if not name or not _series_of(name):
        return None
    folders: list[Path] = []
    filename = ""
    if db is not None:
        episode = db.get(Episode, name)
        if episode:
            filename = episode.filename or ""
            if episode.folder_path:
                folders.append(Path(episode.folder_path))
    folders.append(PROJECT_ROOT / "episodes" / name)
    seen: set[Path] = set()
    for folder in folders:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        if filename:
            candidate = folder / filename
            if candidate.is_file():
                return candidate
        found = next((p for p in folder.iterdir() if p.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}), None)
        if found:
            return found
    return None


def catalog(db: Session, member: Member | None) -> list[dict]:
    rows = list(
        db.scalars(
            select(Puzzle)
            .where(Puzzle.active.is_(True), Puzzle.episode_id.is_not(None))
            .order_by(Puzzle.series, Puzzle.episode_id)
        )
    )
    attempts = {}
    if member:
        attempts = {
            row.puzzle_id: row
            for row in db.scalars(select(PuzzleAttempt).where(PuzzleAttempt.member_id == member.id))
        }
    out = []
    for row in rows:
        attempt = attempts.get(row.id)
        try:
            choices = json.loads(row.choices or "[]")
        except json.JSONDecodeError:
            choices = []
        out.append(
            {
                "id": row.id,
                "episodeId": row.episode_id,
                "series": row.series,
                "title": f"{SERIES_PREFIX.get(row.series or '', row.series)} {row.episode_id}",
                "prompt": row.prompt,
                "choices": choices,
                "hasVideo": video_file(row.episode_id or "", db) is not None,
                "solved": bool(attempt and attempt.finished_at),
                "correct": bool(attempt and attempt.correct) if attempt and attempt.finished_at else None,
                "pointsAwarded": int(attempt.points_awarded) if attempt and attempt.finished_at else 0,
            }
        )
    return out


def start_attempt(db: Session, member: Member, puzzle_id: int) -> dict:
    puzzle = db.get(Puzzle, puzzle_id)
    if not puzzle or not puzzle.active or not puzzle.episode_id:
        raise ValueError("That puzzle is not on the channel.")
    attempt = db.scalar(
        select(PuzzleAttempt).where(
            PuzzleAttempt.member_id == member.id,
            PuzzleAttempt.puzzle_id == puzzle.id,
        )
    )
    if attempt and attempt.finished_at is not None:
        raise ValueError("You already solved this puzzle. Pick a new video.")
    if attempt is None:
        attempt = PuzzleAttempt(member_id=member.id, puzzle_id=puzzle.id, started_at=datetime.now(UTC))
        db.add(attempt)
        safe_flush(db)
    return {
        "puzzleId": puzzle.id,
        "episodeId": puzzle.episode_id,
        "timerStart": TIMER_START,
        "factor": POINTS_FACTOR,
        "startedAt": attempt.started_at.isoformat() if attempt.started_at else datetime.now(UTC).isoformat(),
    }


def submit_answer(db: Session, member: Member, puzzle_id: int, choice: str) -> dict:
    puzzle = db.get(Puzzle, puzzle_id)
    if not puzzle or not puzzle.active or not puzzle.episode_id:
        raise ValueError("That puzzle is not on the channel.")
    attempt = db.scalar(
        select(PuzzleAttempt).where(
            PuzzleAttempt.member_id == member.id,
            PuzzleAttempt.puzzle_id == puzzle.id,
        )
    )
    if attempt and attempt.finished_at is not None:
        raise ValueError("You already solved this puzzle. Pick a new video.")
    now = datetime.now(UTC)
    if attempt is None:
        attempt = PuzzleAttempt(member_id=member.id, puzzle_id=puzzle.id, started_at=now)
        db.add(attempt)
        safe_flush(db)
    started = attempt.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    elapsed = max(0, int((now - started).total_seconds()))
    timer_left = max(1, TIMER_START - elapsed)
    given = (choice or "").strip()
    correct = _normalize(given) == _normalize(puzzle.answer)
    points = timer_left * POINTS_FACTOR if correct else 0
    if correct:
        member.channel_points = int(member.channel_points or 0) + points
        message = f"Yes! +{points} points"
    else:
        message = random.choice(MISS_MESSAGES)
    attempt.finished_at = now
    attempt.choice = given[:128]
    attempt.correct = correct
    attempt.timer_left = timer_left
    attempt.points_awarded = points
    attempt.message = message
    member.last_seen_at = now
    safe_flush(db)
    return {
        "correct": correct,
        "pointsAwarded": points,
        "timerLeft": timer_left,
        "channelPoints": int(member.channel_points or 0),
        "message": message,
        "factor": POINTS_FACTOR,
    }
