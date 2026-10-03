from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base
from app.services import clicks as click_service
from app.services import join as join_service
from app.services import winners as winner_service


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def test_winner_line_matches_brief():
    assert winner_service.winner_line("luna_solve", 3842) == "This weeks winner is luna_solve with 3842"


def test_seed_winner_points_in_requested_range():
    names = set()
    for seed in range(40):
        username, points = winner_service.pick_seed_winner(__import__("random").Random(seed))
        assert username in winner_service.SEED_USERNAMES
        assert 2000 <= points <= 5000
        names.add(username)
    assert len(names) > 1


def test_intro_every_three_days():
    db = _session()
    start = date(2026, 9, 14)
    assert winner_service.is_intro_day(db, start, 3) is True
    winner_service.create_featured_winner(db, {"winnerMode": "seed"}, start, episode_id="ALIEN_01")
    assert winner_service.is_intro_day(db, start + timedelta(days=1), 3) is False
    assert winner_service.is_intro_day(db, start + timedelta(days=2), 3) is False
    assert winner_service.is_intro_day(db, start + timedelta(days=3), 3) is True
    assert winner_service.next_intro_date(db, start + timedelta(days=1), 3) == start + timedelta(days=3)


def test_ensure_featured_winner_reuses_same_day():
    db = _session()
    day = date(2026, 9, 14)
    first = winner_service.ensure_featured_winner(db, {"winnerMode": "seed"}, day, "ALIEN_01")
    second = winner_service.ensure_featured_winner(db, {"winnerMode": "seed"}, day, "ALIEN_01")
    assert first.id == second.id
    assert first.username == second.username
    skipped = winner_service.ensure_featured_winner(db, {"winnerMode": "seed"}, day + timedelta(days=1), "ALIEN_02")
    assert skipped is None


def test_click_from_short_then_signup_awards_points():
    db = _session()
    cfg = Settings(join_min_age=13)
    click_service.record_click(db, visitor_token="abc123456", platform="youtube", episode_id="ALIEN_05")
    click_service.record_click(db, visitor_token="abc123456", platform="youtube", episode_id="ALIEN_05")
    click_service.record_click(db, visitor_token="abc123456", platform="instagram", episode_id="ALIEN_06")
    member = join_service.register_member(
        db, cfg, username="tapmaster", age=16, password="pass", country="Pakistan", region="Sindh"
    )
    clicks = click_service.attach_visitor(db, member, "abc123456")
    assert clicks == 2
    assert click_service.member_points(db, member.id, 2500) == 5000
    board = click_service.leaderboard(db, 2500)
    assert board[0]["username"] == "tapmaster"
    featured = winner_service.create_featured_winner(db, {"winnerMode": "clicks", "pointsMultiplier": 2500}, date(2026, 9, 17))
    assert featured.username == "tapmaster"
    assert featured.points == 5000
    assert featured.source == "clicks"


def test_clicks_mode_falls_back_to_seed_when_empty():
    db = _session()
    row = winner_service.create_featured_winner(db, {"winnerMode": "clicks", "pointsMultiplier": 2500}, date(2026, 9, 14))
    assert row.source == "seed"
    assert row.username in winner_service.SEED_USERNAMES
    assert 2000 <= row.points <= 5000


def test_join_url_uses_public_site_path():
    from app.config import Settings

    assert Settings(public_site_url="https://play.example.com").join_url() == "https://play.example.com/join"
    assert Settings(public_site_url="https://play.example.com/join").join_url() == "https://play.example.com/join"
    assert Settings(public_site_url="").join_url().endswith("/join")


def test_tracked_join_url_and_caption_stamp():
    url = click_service.tracked_join_url("http://127.0.0.1:8088/join", "youtube", "ALIEN_01")
    assert "src=youtube" in url
    assert "ep=ALIEN_01" in url
    stamped = click_service.stamp_caption_urls(
        "Join here: http://127.0.0.1:8088/join\n#Shorts",
        "http://127.0.0.1:8088/join",
        "instagram",
        "BLUR_02",
    )
    assert "src=instagram" in stamped
    assert "ep=BLUR_02" in stamped
    assert stamped.count("http://127.0.0.1:8088/join") == 1


def test_manual_feature_uses_member_points():
    db = _session()
    cfg = Settings(join_min_age=13)
    member = join_service.register_member(
        db, cfg, username="realuser", age=18, password="pass", country="Pakistan", region=None
    )
    click_service.record_click(db, visitor_token="tokentoken", platform="youtube", episode_id="JIG_01", member_id=member.id)
    row = winner_service.create_featured_winner(
        db,
        {"winnerMode": "seed", "pointsMultiplier": 2500},
        date(2026, 9, 20),
        username=member.username,
        member_id=member.id,
        source="manual",
    )
    assert row.username == "realuser"
    assert row.points == 2500
    assert row.source == "manual"


def test_winner_template_picks_mp4(tmp_path, monkeypatch):
    from app.services import overlay as overlay_service

    folder = tmp_path / "winner"
    folder.mkdir()
    (folder / "readme.txt").write_text("ignore", encoding="utf-8")
    (folder / "puz03.mp4").write_bytes(b"fake")
    (folder / "other.MOV").write_bytes(b"fake")
    monkeypatch.setattr(overlay_service, "WINNER_DIR", folder)
    found = overlay_service.winner_template()
    assert found is not None
    assert found.name == "other.MOV"


def test_winner_template_missing_folder(tmp_path, monkeypatch):
    from app.services import overlay as overlay_service

    monkeypatch.setattr(overlay_service, "WINNER_DIR", tmp_path / "nope")
    assert overlay_service.winner_template() is None


def test_winner_name_is_uppercased_for_intro():
    from app.services import overlay as overlay_service

    assert overlay_service.display_name("Mohammad") == "MOHAMMAD"
    assert overlay_service.display_name("luna_solve") == "LUNA SOLVE"


def test_points_label_uses_dot_thousands():
    from app.services import overlay as overlay_service

    assert overlay_service.format_points_label(12300) == "12.300 pts"
    assert overlay_service.format_points_label(3343) == "3.343 pts"


def test_name_fontsize_shrinks_for_long_names():
    from app.services import overlay as overlay_service

    assert overlay_service._mid_fontsize("MOHAMMAD") == 150
    assert overlay_service._mid_fontsize("LUNA SOLVE") == 118
    assert overlay_service._mid_fontsize("VERY LONG USERNAME HERE") < overlay_service._mid_fontsize("MOHAMMAD")


def test_winner_titles_use_large_name():
    from app.services import overlay as overlay_service
    from types import SimpleNamespace

    lines = overlay_service._winner_phase_lines(SimpleNamespace(username="Mohammad", points=12300))
    texts = [row[0] for row in lines]
    sizes = {row[0]: row[1] for row in lines}
    assert texts == ["WINNER of the WEEK", "MOHAMMAD", "12.300 pts"]
    assert sizes["MOHAMMAD"] == 150
    assert sizes["WINNER of the WEEK"] >= 72
    assert sizes["12.300 pts"] >= 90


def test_text_starts_at_two_seconds_and_cta_holds_two():
    from app.services import overlay as overlay_service

    switch = overlay_service._text_switch_at(10.05)
    assert overlay_service.TEXT_START == 2.0
    assert switch == 8.05
    assert 10.05 - switch >= overlay_service.CTA_SECONDS
