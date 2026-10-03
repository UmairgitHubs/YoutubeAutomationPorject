from datetime import UTC, date, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base
from app.models import Puzzle, PuzzleAttempt
from app.services import channel as channel_service
from app.services import join as join_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _kid(db: Session):
    return join_service.register_member(
        db, Settings(join_min_age=13), username="kidstar", age=14, password="pass", country="Pakistan", region=None
    )


def _video_puzzle(db: Session, episode_id: str = "JIG_01") -> Puzzle:
    spec = channel_service._spec_for(episode_id)
    row = Puzzle(
        prompt=spec["prompt"],
        answer=spec["answer"],
        choices=__import__("json").dumps(spec["choices"]),
        active=True,
        episode_id=episode_id,
        series="JIG",
    )
    db.add(row)
    db.flush()
    return row


def test_correct_answer_uses_timer_and_factor():
    db = _session()
    member = _kid(db)
    puzzle = _video_puzzle(db)
    start = channel_service.start_attempt(db, member, puzzle.id)
    assert start["timerStart"] == 100
    assert start["factor"] == 9
    result = channel_service.submit_answer(db, member, puzzle.id, puzzle.answer)
    assert result["correct"] is True
    assert result["timerLeft"] == 100
    assert result["pointsAwarded"] == 900
    assert result["channelPoints"] == 900
    assert member.channel_points == 900


def test_wrong_answer_keeps_points_and_uses_miss_copy():
    db = _session()
    member = _kid(db)
    puzzle = _video_puzzle(db)
    channel_service.start_attempt(db, member, puzzle.id)
    result = channel_service.submit_answer(db, member, puzzle.id, "A car")
    assert result["correct"] is False
    assert result["pointsAwarded"] == 0
    assert member.channel_points == 0
    assert result["message"] in channel_service.MISS_MESSAGES


def test_same_puzzle_cannot_be_solved_twice():
    db = _session()
    member = _kid(db)
    puzzle = _video_puzzle(db)
    channel_service.submit_answer(db, member, puzzle.id, puzzle.answer)
    try:
        channel_service.start_attempt(db, member, puzzle.id)
        raise AssertionError("expected lock")
    except ValueError as exc:
        assert "already" in str(exc).lower()
    try:
        channel_service.submit_answer(db, member, puzzle.id, puzzle.answer)
        raise AssertionError("expected lock")
    except ValueError as exc:
        assert "already" in str(exc).lower()
    assert member.channel_points == 900


def test_slow_correct_answer_keeps_minimum_points():
    db = _session()
    member = _kid(db)
    puzzle = _video_puzzle(db)
    channel_service.start_attempt(db, member, puzzle.id)
    attempt = db.scalar(
        select(PuzzleAttempt).where(PuzzleAttempt.member_id == member.id, PuzzleAttempt.puzzle_id == puzzle.id)
    )
    attempt.started_at = datetime.now(UTC) - timedelta(seconds=150)
    result = channel_service.submit_answer(db, member, puzzle.id, puzzle.answer)
    assert result["correct"] is True
    assert result["timerLeft"] == 1
    assert result["pointsAwarded"] == 9
    assert member.channel_points == 9


def test_points_add_up_across_puzzles():
    db = _session()
    member = _kid(db)
    first = _video_puzzle(db, "JIG_01")
    second = _video_puzzle(db, "JIG_02")
    channel_service.submit_answer(db, member, first.id, first.answer)
    channel_service.submit_answer(db, member, second.id, second.answer)
    assert member.channel_points == 1800


def test_daily_riddles_still_work():
    db = _session()
    join_service.seed_puzzles(db)
    member = _kid(db)
    today = date(2026, 9, 10)
    puzzles = join_service.todays_puzzles(db, today)
    answers = [puzzles[0].answer, "", ""]
    result = join_service.submit_run(db, member, today, answers, 12000)
    assert result["score"] >= 1
    assert member.channel_points == 0


def test_miss_list_has_at_least_twenty():
    assert len(channel_service.MISS_MESSAGES) >= 20
    assert len(set(channel_service.MISS_MESSAGES)) >= 20


def test_daily_riddles_ignore_channel_videos():
    db = _session()
    join_service.seed_puzzles(db)
    _video_puzzle(db, "JIG_01")
    _video_puzzle(db, "ALIEN_01")
    today = date(2026, 9, 10)
    puzzles = join_service.todays_puzzles(db, today)
    assert puzzles
    assert all(not row.episode_id for row in puzzles)


def test_seed_puzzles_still_runs_when_channel_exists(tmp_path):
    db = _session()
    pack = tmp_path / "JIG_07"
    pack.mkdir()
    (pack / "metadata.json").write_text('{"id":"JIG_07"}', encoding="utf-8")
    assert channel_service.seed_channel_puzzles(db, tmp_path) == 1
    join_service.seed_puzzles(db)
    riddles = list(db.scalars(select(Puzzle).where(Puzzle.episode_id.is_(None))))
    videos = list(db.scalars(select(Puzzle).where(Puzzle.episode_id.is_not(None))))
    assert len(riddles) >= 3
    assert len(videos) == 1
    assert all(row.episode_id is None for row in join_service.todays_puzzles(db, date(2026, 9, 10)))


def test_channel_http_flow_one_try_and_score():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.api.join import router
    from app.database import get_db

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    join_service.seed_puzzles(db)
    puzzle = _video_puzzle(db, "JIG_01")
    db.commit()
    answer = puzzle.answer
    db.close()

    def override():
        session = Session()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    signed = client.post("/api/join/signup", json={"username": "chanplay", "age": 14, "password": "pass"})
    assert signed.status_code == 200
    assert signed.json()["me"]["channelPoints"] == 0
    catalog = client.get("/api/join/channel").json()
    assert catalog["puzzles"]
    hit = next(row for row in catalog["puzzles"] if row["episodeId"] == "JIG_01")
    pid = hit["id"]
    start = client.post(f"/api/join/channel/puzzles/{pid}/start")
    assert start.status_code == 200
    assert start.json()["timerStart"] == 100
    result = client.post(f"/api/join/channel/puzzles/{pid}/answer", json={"choice": answer})
    assert result.status_code == 200
    body = result.json()
    assert body["correct"] is True
    assert body["pointsAwarded"] == 900
    assert body["channelPoints"] == 900
    locked = client.post(f"/api/join/channel/puzzles/{pid}/start")
    assert locked.status_code == 400
    daily = client.get("/api/join/puzzles")
    assert daily.status_code == 200
    prompts = [item["prompt"] for item in daily.json()["puzzles"]]
    assert len(prompts) == 3
    assert all("Jig Puzzle" not in item and "Alien Monkeys" not in item for item in prompts)
