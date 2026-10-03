from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base
from app.models import Episode
from app.services import campaigns as campaign_service
from app.services import join as join_service
from app.services import queue as queue_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_expand_schedule_loops_until_end_date():
    start = date(2026, 9, 10)
    end = date(2026, 9, 12)
    slots = campaign_service.expand_schedule(start, end, videos_per_day=2, video_count=3, loop=True)
    assert len(slots) == 6
    assert [index for _, index in slots] == [0, 1, 2, 0, 1, 2]
    assert slots[0][0] == start
    assert slots[-1][0] == end


def test_expand_schedule_stops_without_loop():
    start = date(2026, 9, 10)
    end = date(2026, 9, 20)
    slots = campaign_service.expand_schedule(start, end, videos_per_day=2, video_count=3, loop=False)
    assert len(slots) == 3
    assert [index for _, index in slots] == [0, 1, 2]


def test_parse_caption_file_csv_and_blocks():
    names = ["one.mp4", "two.mp4"]
    csv_text = "filename,title,description\none.mp4,First,Hello\ntwo.mp4,Second,There\n"
    rows = campaign_service.parse_caption_file(csv_text, names)
    assert rows[0]["title"] == "First"
    assert rows[1]["description"] == "There"
    blocks = campaign_service.parse_caption_file("Alpha | a\n\nBeta | b", names)
    assert blocks[0]["title"] == "Alpha"
    assert blocks[1]["description"] == "b"


def test_parse_platforms_radio_presets():
    assert campaign_service.parse_platforms("youtube") == ["youtube"]
    assert campaign_service.parse_platforms("youtube_instagram") == ["youtube", "instagram"]
    assert campaign_service.parse_platforms("all") == ["youtube", "tiktok", "instagram"]


def test_create_campaign_skips_unselected_platforms(tmp_path):
    db = _session()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0" * 2000)
    cfg = Settings(episodes_dir=tmp_path / "episodes")
    start = date(2026, 9, 10)
    campaign = campaign_service.create_campaign(
        db,
        cfg,
        name="Test batch",
        platforms=["youtube", "instagram"],
        start=start,
        end=start + timedelta(days=1),
        videos_per_day=1,
        loop=True,
        video_paths=[video],
        captions=[{"title": "Hello", "description": "World"}],
    )
    assert campaign.slot_count == 2
    episodes = db.query(Episode).order_by(Episode.queue_order).all()
    assert len(episodes) == 2
    assert all(ep.tiktok_status == "skipped" for ep in episodes)
    assert all(ep.youtube_status == "pending" for ep in episodes)
    assert (Path(episodes[0].folder_path) / "clip.mp4").exists()
    due = queue_service.next_unpublished(db, limit=1, today=start)
    assert [ep.episode_id for ep in due] == [episodes[0].episode_id]
    later = queue_service.next_unpublished(db, limit=1, today=start + timedelta(days=3))
    assert [ep.episode_id for ep in later] == [episodes[0].episode_id]


def test_join_rejects_underage_and_picks_winner():
    db = _session()
    join_service.seed_puzzles(db)
    cfg = Settings(join_min_age=13)
    try:
        join_service.register_member(
            db, cfg, username="kid", age=10, password="pass", country="Pakistan", region="Sindh"
        )
        raise AssertionError("expected age gate")
    except ValueError as exc:
        assert "13" in str(exc)

    member = join_service.register_member(
        db, cfg, username="Mohammad", age=14, password="pass", country="Pakistan", region="Sindh"
    )
    today = date(2026, 9, 10)
    puzzles = join_service.todays_puzzles(db, today)
    answers = [puzzles[0].answer, "", ""]
    result = join_service.submit_run(db, member, today, answers, 12000)
    assert result["score"] >= 1
    assert result["youWon"] is True
    assert "MOHAMMAD" in (result["winner"]["line"] or "")
    overlay = join_service.overlay_winner(db, today + timedelta(days=1))
    assert overlay is not None
    assert overlay.username == "Mohammad"
