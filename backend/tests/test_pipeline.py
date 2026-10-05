import hashlib
import json
from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base
from app.models import Episode, PublishEvent
from app.services import queue as queue_service
from app.services.orchestrator import publish_episode


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _episode(db: Session, episode_id: str = "EP100", **kwargs) -> Episode:
    ep = Episode(
        episode_id=episode_id,
        title="Test episode",
        description="A unit-test package.",
        filename=f"{episode_id}.mp4",
        tags_hashtags='["test"]',
        scheduled_date=date(2026, 8, 20),
        queue_order=kwargs.pop("queue_order", 1),
        **kwargs,
    )
    db.add(ep)
    db.flush()
    return ep


def test_overall_status_attention_and_published():
    db = _session()
    ep = _episode(db, youtube_status="success", tiktok_status="failed", instagram_status="success")
    assert ep.overall_status() == "attention"
    ep.tiktok_status = "success"
    assert ep.overall_status() == "published"


def test_overall_status_treats_skipped_as_complete():
    db = _session()
    ep = _episode(db, youtube_status="success", tiktok_status="skipped", instagram_status="success")
    assert ep.overall_status() == "published"


def test_skip_only_pending_platforms():
    db = _session()
    ep = _episode(db, youtube_status="success", tiktok_status="pending", instagram_status="pending")
    queue_service.skip(db, ep.episode_id)
    assert ep.youtube_status == "success"
    assert ep.tiktok_status == "skipped"
    assert ep.instagram_status == "skipped"


def test_reorder_swaps_queue_order():
    db = _session()
    _episode(db, "EP101", queue_order=1)
    _episode(db, "EP102", queue_order=2)
    ids = queue_service.reorder(db, "EP101", 1)
    assert ids == ["EP102", "EP101"]


def test_dry_run_publish_is_idempotent():
    db = _session()
    _episode(db, "EP200")
    cfg = Settings(dry_run=True, openai_api_key="")
    first = publish_episode(db, cfg, "EP200")
    assert first["overall"] == "published"
    assert first["dry_run"] is True
    second = publish_episode(db, cfg, "EP200")
    assert second["overall"] == "published"
    events = list(db.scalars(select(PublishEvent).where(PublishEvent.episode_id == "EP200")))
    assert any("Already published" in (row.detail or "") for row in events)
    ep = db.get(Episode, "EP200")
    assert ep.youtube_video_id == "dry_yt_EP200"
    assert ep.tiktok_status == "skipped"
    assert ep.instagram_media_id == "dry_ig_EP200"


def test_tiktok_authorization_url_requires_credentials():
    from app.services import tiktok_oauth

    cfg = Settings(tiktok_client_key="", tiktok_client_secret="")
    try:
        tiktok_oauth.authorization_url(cfg)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Redirect URI" in str(exc)


def test_tiktok_authorization_url_includes_direct_post_scope(tmp_path, monkeypatch):
    from app.services import tiktok_oauth

    monkeypatch.setattr(tiktok_oauth, "STATE_PATH", tmp_path / "tiktok_oauth_state.txt")
    cfg = Settings(
        tiktok_client_key="test_key",
        tiktok_client_secret="test_secret",
        host="127.0.0.1",
        port=8088,
    )
    url = tiktok_oauth.authorization_url(cfg)
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    assert "client_key=test_key" in url
    assert "video.publish" in url
    assert "user.info.basic" in url
    assert "code_challenge_method=S256" in url
    assert "code_challenge=" in url
    assert "api%2Fauth%2Ftiktok%2Fcallback" in url or "/api/auth/tiktok/callback" in url
    session = json.loads((tmp_path / "tiktok_oauth_state.txt").read_text(encoding="utf-8"))
    assert session["state"]
    assert len(session["verifier"]) == 64


def test_tiktok_pkce_challenge_is_hex_sha256():
    from app.services import tiktok_oauth

    verifier, challenge = tiktok_oauth._pkce_pair()
    assert len(verifier) == 64
    assert challenge == hashlib.sha256(verifier.encode("ascii")).hexdigest()
    assert len(challenge) == 64


def test_youtube_invalid_grant_asks_to_reconnect():
    from app.publishers.youtube import RECONNECT_MSG, _friendly_youtube_error

    err = _friendly_youtube_error(Exception("('invalid_grant: Bad Request', {'error': 'invalid_grant'})"))
    assert err == RECONNECT_MSG
    assert "Reconnect" in err


def test_youtube_redirect_uri_follows_browser_host():
    from app.services import youtube_oauth

    cfg = Settings(host="127.0.0.1", port=8088, youtube_redirect_uri="")
    assert youtube_oauth.redirect_uri(cfg) == "http://127.0.0.1:8088/api/auth/youtube/callback"
    assert youtube_oauth.redirect_uri(cfg, "http://localhost:8088") == "http://localhost:8088/api/auth/youtube/callback"
    both = youtube_oauth.registered_redirects(cfg, "http://localhost:8088/api/auth/youtube/callback")
    assert "http://127.0.0.1:8088/api/auth/youtube/callback" in both
    assert "http://localhost:8088/api/auth/youtube/callback" in both


def test_instagram_uses_instagram_graph_for_igaa_tokens():
    from app.publishers.instagram import _graph_base

    assert _graph_base("IGAAacExampleToken") == "https://graph.instagram.com/v21.0"
    assert _graph_base("EAABsbCS1ZCgs") == "https://graph.facebook.com/v21.0"


def test_instagram_live_publish_needs_local_video(tmp_path):
    from app.publishers.instagram import InstagramPublisher

    cfg = Settings(instagram_access_token="IGAAtest", instagram_ig_user_id="123", dry_run=False)
    ep = Episode(
        episode_id="IG001",
        title="Local reel",
        filename="missing.mp4",
        folder_path=str(tmp_path),
    )
    result = InstagramPublisher().publish(ep, cfg, dry_run=False)
    assert result.success is False
    assert "Video file is missing" in (result.error or "")


def test_instagram_reel_filter_fills_frame():
    from app.services.reel_prepare import PREP_VERSION, REEL_VF

    assert "force_original_aspect_ratio=increase" in REEL_VF
    assert "crop=1080:1920" in REEL_VF
    assert "pad=" not in REEL_VF
    assert PREP_VERSION == "cover1"


def test_captions_fallback_without_openai_key():
    from types import SimpleNamespace

    from app.services.captions import generate_captions

    cfg = Settings(openai_api_key="")
    ep = SimpleNamespace(
        episode_id="JIG_01",
        title="Jigsaw 01",
        description="A Puzmania short from the Jigsaw series.",
        tags_hashtags='["puzmania", "jigsaw"]',
    )
    caps = generate_captions(ep, cfg)
    assert caps.source == "fallback"
    assert caps.error
    assert "JIG PUZZLE #01" in caps.youtube_title
    assert "Welcome to PUZMANIA" in caps.instagram_caption
    assert "Episode: 01" in caps.instagram_caption
    assert "#PUZMANIA" in caps.instagram_caption
    assert "#JigPuzzle" in caps.instagram_caption


def test_captions_use_openai_json(monkeypatch):
    from types import SimpleNamespace

    from app.services import captions as captions_mod

    class _Resp:
        status_code = 200
        text = ""

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "youtube_title": "Play Puzmania now",
                                    "youtube_description": "Tap to play.\n\n#Shorts #Puzmania",
                                    "instagram_caption": "Puzzle time #Puzmania #Reels",
                                    "tiktok_caption": "Puzzle time #Puzmania",
                                }
                            )
                        }
                    }
                ]
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return _Resp()

    captured: dict = {}

    class _CaptureClient(_Client):
        def post(self, *args, **kwargs):
            captured["json"] = kwargs.get("json")
            return _Resp()

    monkeypatch.setattr(captions_mod.httpx, "Client", _CaptureClient)
    monkeypatch.setattr(captions_mod, "_preview_frames", lambda video, count=3: [b"fake-jpeg-bytes"])
    cfg = Settings(openai_api_key="sk-test", openai_model="gpt-4o-mini")
    ep = SimpleNamespace(
        episode_id="JIG_01",
        title="Jigsaw 01",
        description="desc",
        tags_hashtags='["jigsaw"]',
    )
    caps = captions_mod.generate_captions(ep, cfg)
    assert caps.source == "openai"
    assert caps.youtube_title == "Play Puzmania now"
    assert caps.instagram_caption.startswith("Puzzle time")
    user = captured["json"]["messages"][1]["content"]
    assert user[0]["type"] == "text"
    assert "Jig Puzzle" in user[0]["text"]
    assert user[1]["type"] == "image_url"
    assert user[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_series_info_from_episode_id():
    from app.services.captions import series_info

    jig = series_info("JIG_07")
    assert jig["name"] == "Jig Puzzle"
    assert jig["hook"] == "JIG PUZZLE"
    assert jig["episode"] == "07"
    alien = series_info("ALIEN_03")
    assert alien["name"] == "Alien Monkeys"
    assert alien["episode"] == "03"


def test_openai_error_message_hides_key():
    from app.services.captions import _public_openai_error

    msg = _public_openai_error("Incorrect API key provided: sk-proj-abc")
    assert "sk-proj" not in msg
    assert "OpenAI rejected the API key" in msg


def test_publish_job_progress_snapshot():
    from app.services.publish_jobs import PublishJob

    job = PublishJob(id="abc", episode_id="JIG_01")
    job.emit("caption", "Writing captions…", 40)
    job.emit("caption", "Captions ready.", 100, "success")
    job.emit("youtube", "Uploading to YouTube… 50%", 50)
    snap = job.snapshot()
    assert snap["steps"][0]["status"] == "success"
    assert snap["steps"][1]["percent"] == 50
    assert snap["percent"] >= 10
    assert snap["state"] == "running"


def test_tmpfiles_direct_url_rewrite():
    from app.services.media_stage import _tmpfiles

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"url": "http://tmpfiles.org/99/clip.mp4"}}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return _Resp()

    import app.services.media_stage as media_stage

    original = media_stage.httpx.Client
    media_stage.httpx.Client = _Client
    try:
        url = _tmpfiles(__import__("pathlib").Path(__file__))
    finally:
        media_stage.httpx.Client = original
    assert url == "https://tmpfiles.org/dl/99/clip.mp4"


def test_scan_removes_episodes_missing_from_disk(tmp_path):
    from app.services.library import scan_episode_folder

    db = _session()
    keep = tmp_path / "KEEP_01"
    keep.mkdir()
    (keep / "clip.mp4").write_bytes(b"fake")
    (keep / "metadata.json").write_text(
        json.dumps({"id": "KEEP_01", "title": "Keep", "filename": "clip.mp4"}),
        encoding="utf-8",
    )
    _episode(db, "KEEP_01")
    _episode(db, "GONE_01")
    db.add(PublishEvent(episode_id="GONE_01", platform="system", result="info", detail="stale"))
    db.flush()

    cfg = Settings(episodes_dir=tmp_path)
    seen, removed = scan_episode_folder(db, cfg)
    db.flush()
    assert "KEEP_01" in seen
    assert removed == ["GONE_01"]
    assert db.get(Episode, "KEEP_01") is not None
    assert db.get(Episode, "GONE_01") is None
    assert list(db.scalars(select(PublishEvent).where(PublishEvent.episode_id == "GONE_01"))) == []


def test_queue_rotates_one_from_each_series():
    db = _session()
    for ep_id, order in [
        ("ALIEN_01", 1),
        ("ALIEN_02", 2),
        ("BLUR_01", 3),
        ("JIG_01", 4),
        ("JIG_02", 5),
        ("BLUR_02", 6),
    ]:
        _episode(db, ep_id, queue_order=order)
    ids = queue_service.interleave_series(db)
    assert ids == ["JIG_01", "ALIEN_01", "BLUR_01", "JIG_02", "ALIEN_02", "BLUR_02"]
    due = queue_service.next_unpublished(db, limit=1, today=date(2026, 8, 20))
    assert due[0].episode_id == "JIG_01"
