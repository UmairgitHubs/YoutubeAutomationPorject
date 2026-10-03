from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, Settings
from app.database import safe_flush
from app.models import DailyWinner, Member, Puzzle, PuzzleRun

log = logging.getLogger("puzmania.join")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
COOKIE_NAME = "puz_join"
PUZZLES_PER_RUN = 3
RUN_SECONDS = 90
SECRET_PATH = PROJECT_ROOT / "data" / "join_secret.txt"

SEED_PUZZLES = [
    {
        "prompt": "I have cities but no houses, forests but no trees, and water but no fish. What am I?",
        "answer": "a map",
        "choices": ["A desert", "A map", "A cloud", "A riddle"],
    },
    {
        "prompt": "What has keys but cannot open a door?",
        "answer": "a piano",
        "choices": ["A piano", "A locksmith", "A backpack", "A clock"],
    },
    {
        "prompt": "I am tall when I am young and short when I am old. What am I?",
        "answer": "a candle",
        "choices": ["A tree", "A pencil", "A candle", "A mountain"],
    },
    {
        "prompt": "What can you catch but not throw?",
        "answer": "a cold",
        "choices": ["A ball", "A cold", "A fish", "A hat"],
    },
    {
        "prompt": "If you have me, you want to share me. If you share me, you have not got me. What am I?",
        "answer": "a secret",
        "choices": ["A secret", "A sandwich", "A smile", "A coin"],
    },
    {
        "prompt": "What goes up but never comes down?",
        "answer": "your age",
        "choices": ["A balloon", "Your age", "The sun", "A kite"],
    },
]


def seed_puzzles(db: Session) -> None:
    if db.scalar(select(Puzzle.id).where(Puzzle.episode_id.is_(None))) is not None:
        return
    for row in SEED_PUZZLES:
        db.add(
            Puzzle(
                prompt=row["prompt"],
                answer=row["answer"],
                choices=json.dumps(row["choices"]),
                active=True,
            )
        )
    safe_flush(db)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000)
    return f"pbkdf2$sha256$120000${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, algo, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    if kind != "pbkdf2" or algo != "sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(rounds))
    return hmac.compare_digest(check.hex(), digest)


def session_secret() -> bytes:
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SECRET_PATH.is_file():
        return SECRET_PATH.read_bytes().strip() or secrets.token_bytes(32)
    raw = secrets.token_hex(32).encode("ascii")
    SECRET_PATH.write_bytes(raw)
    return raw


def make_session_token(member_id: int) -> str:
    stamp = str(int(datetime.now(UTC).timestamp()))
    body = f"{member_id}.{stamp}"
    sig = hmac.new(session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def member_id_from_token(token: str | None) -> int | None:
    if not token or token.count(".") != 2:
        return None
    member_id, stamp, sig = token.split(".")
    body = f"{member_id}.{stamp}"
    expect = hmac.new(session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        issued = datetime.fromtimestamp(int(stamp), UTC)
    except ValueError:
        return None
    if datetime.now(UTC) - issued > timedelta(days=30):
        return None
    try:
        return int(member_id)
    except ValueError:
        return None


def register_member(
    db: Session,
    settings: Settings,
    *,
    username: str,
    age: int,
    password: str,
    country: str | None,
    region: str | None,
) -> Member:
    name = (username or "").strip()
    if not USERNAME_RE.match(name):
        raise ValueError("Username must be 3–20 letters, numbers, or underscores.")
    if age < int(settings.join_min_age):
        raise ValueError(
            f"Members must be {settings.join_min_age} or older. Ask a parent to create the account."
        )
    if age > 120:
        raise ValueError("Please enter a real age.")
    if len(password or "") < 4:
        raise ValueError("Choose a password with at least 4 characters.")
    if db.scalar(select(Member).where(Member.username.ilike(name))):
        raise ValueError("That username is already taken.")
    member = Member(
        username=name,
        age=int(age),
        password_hash=hash_password(password),
        country=(country or "").strip() or None,
        region=(region or "").strip() or None,
        last_seen_at=datetime.now(UTC),
    )
    db.add(member)
    safe_flush(db)
    return member


def login_member(db: Session, username: str, password: str) -> Member:
    member = db.scalar(select(Member).where(Member.username.ilike((username or "").strip())))
    if not member or not verify_password(password, member.password_hash):
        raise ValueError("Username or password is not right.")
    member.last_seen_at = datetime.now(UTC)
    safe_flush(db)
    return member


def member_out(member: Member, clicks: int = 0, points: int = 0) -> dict:
    return {
        "id": member.id,
        "username": member.username,
        "age": member.age,
        "country": member.country,
        "region": member.region,
        "clicks": clicks,
        "points": points,
    }


def lookup_geo(ip: str | None) -> dict[str, str | None]:
    address = (ip or "").split(",")[0].strip()
    if not address or address in {"127.0.0.1", "::1"} or address.startswith(("192.168.", "10.", "172.16.")):
        return {"country": None, "region": None}
    try:
        with httpx.Client(timeout=4.0) as client:
            response = client.get(f"http://ip-api.com/json/{address}?fields=status,country,regionName")
            data = response.json()
        if isinstance(data, dict) and data.get("status") == "success":
            return {
                "country": data.get("country") or None,
                "region": data.get("regionName") or None,
            }
    except Exception:
        log.info("Geo lookup failed for %s", address)
    return {"country": None, "region": None}


def todays_puzzles(db: Session, play_date: date) -> list[Puzzle]:
    rows = [
        row
        for row in db.scalars(
            select(Puzzle)
            .where(Puzzle.active.is_(True), Puzzle.episode_id.is_(None))
            .order_by(Puzzle.id)
        )
        if row
    ]
    if not rows:
        return []
    start = play_date.toordinal() % len(rows)
    picked: list[Puzzle] = []
    for offset in range(len(rows)):
        picked.append(rows[(start + offset) % len(rows)])
        if len(picked) >= min(PUZZLES_PER_RUN, len(rows)):
            break
    return picked


def puzzle_public(row: Puzzle) -> dict:
    try:
        choices = json.loads(row.choices or "[]")
    except json.JSONDecodeError:
        choices = []
    return {"id": row.id, "prompt": row.prompt, "choices": choices}


def start_run(db: Session, member: Member, play_date: date) -> PuzzleRun:
    existing = db.scalar(
        select(PuzzleRun).where(PuzzleRun.member_id == member.id, PuzzleRun.play_date == play_date)
    )
    if existing and existing.finished_at is not None:
        raise ValueError("You already played today’s puzzles. Come back tomorrow.")
    if existing:
        return existing
    puzzles = todays_puzzles(db, play_date)
    run = PuzzleRun(
        member_id=member.id,
        play_date=play_date,
        max_score=len(puzzles),
        answers="[]",
    )
    db.add(run)
    safe_flush(db)
    return run


def submit_run(db: Session, member: Member, play_date: date, answers: list, elapsed_ms: int) -> dict:
    run = start_run(db, member, play_date)
    if run.finished_at is not None:
        raise ValueError("You already played today’s puzzles. Come back tomorrow.")
    puzzles = todays_puzzles(db, play_date)
    score = 0
    detail = []
    for index, puzzle in enumerate(puzzles):
        given = ""
        if index < len(answers):
            given = str(answers[index] or "").strip()
        ok = _normalize(given) == _normalize(puzzle.answer) or _normalize(given) in _normalize(puzzle.answer)
        if ok:
            score += 1
        detail.append({"id": puzzle.id, "correct": ok})
    limited = max(0, min(int(elapsed_ms or 0), RUN_SECONDS * 1000 + 5000))
    run.score = score
    run.elapsed_ms = limited
    run.max_score = len(puzzles)
    run.answers = json.dumps(answers)
    run.finished_at = datetime.now(UTC)
    safe_flush(db)
    winner = refresh_daily_winner(db, play_date)
    return {
        "score": score,
        "maxScore": len(puzzles),
        "elapsedMs": limited,
        "seconds": RUN_SECONDS,
        "results": detail,
        "winner": winner_out(winner) if winner else None,
        "youWon": bool(winner and winner.member_id == member.id),
    }


def refresh_daily_winner(db: Session, play_date: date) -> DailyWinner | None:
    runs = list(
        db.scalars(
            select(PuzzleRun).where(
                PuzzleRun.play_date == play_date,
                PuzzleRun.finished_at.is_not(None),
            )
        )
    )
    if not runs:
        return None
    runs.sort(key=lambda row: (-row.score, row.elapsed_ms or 10**9, row.finished_at or datetime.now(UTC)))
    best = runs[0]
    member = db.get(Member, best.member_id)
    if not member:
        return None
    row = db.get(DailyWinner, play_date)
    if row is None:
        row = DailyWinner(play_date=play_date, member_id=member.id, username=member.username)
        db.add(row)
    row.member_id = member.id
    row.username = member.username
    row.country = member.country
    row.score = best.score
    row.elapsed_ms = best.elapsed_ms
    safe_flush(db)
    return row


def winner_for(db: Session, play_date: date) -> DailyWinner | None:
    return db.get(DailyWinner, play_date) or refresh_daily_winner(db, play_date)


def overlay_winner(db: Session, today: date) -> DailyWinner | None:
    """Yesterday’s winner is shown on today’s videos; fall back to today if needed."""
    return winner_for(db, today - timedelta(days=1)) or winner_for(db, today)


def winner_out(row: DailyWinner | None) -> dict | None:
    if not row:
        return None
    return {
        "date": row.play_date.isoformat(),
        "username": row.username,
        "country": row.country,
        "score": row.score,
        "line": f"Our New Winner is {(row.username or '').upper()} from {row.country or 'Puzmania'}",
    }


def winners_list(db: Session, limit: int = 14) -> list[dict]:
    rows = list(db.scalars(select(DailyWinner).order_by(DailyWinner.play_date.desc()).limit(limit)))
    return [winner_out(row) for row in rows if row]


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())
