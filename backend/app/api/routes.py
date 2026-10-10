from html import escape
from datetime import date
from pathlib import Path
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Campaign, Episode, Member, PublishEvent
from app.publishers import PUBLISHERS
from app.schemas import FeatureWinnerIn, PlatformPatch, ReorderIn, SettingsIn
from app.services import campaigns as campaign_service
from app.services import clicks as click_service
from app.services import desktop as desktop_service
from app.services import join as join_service
from app.services import library, orchestrator, publish_jobs, queue as queue_service
from app.services import gdrive, tiktok_oauth, youtube_oauth
from app.services import winners as winner_service
from app.services.scheduler import reschedule
from app.services.studio_auth import require_studio
from app.services.serialize import (
    episode_out,
    history_out,
    load_settings,
    next_run_at,
    queue_ids,
    save_settings,
    settings_out,
)

router = APIRouter(prefix="/api", dependencies=[Depends(require_studio)])

PLATFORM_META = {
    "youtube": {
        "name": "YouTube",
        "api": "Data API v3",
        "method": "videos.insert · thumbnails.set",
        "note": "OAuth consent is required once per channel. If uploads fail with invalid_grant, reconnect here. Testing-mode Google apps expire the login after 7 days.",
    },
    "tiktok": {
        "name": "TikTok",
        "api": "Content Posting API",
        "method": "Direct Post",
        "note": "Connect from this page. Unaudited apps can only post privately. Public auto-publish needs TikTok’s compliance audit.",
    },
    "instagram": {
        "name": "Instagram",
        "api": "Graph API",
        "method": "local file → Reels container → publish",
        "note": "Reads the episode .mp4 from disk, crops it to a full-bleed 9:16 reel, then publishes. ChatGPT writes the caption first.",
    },
}


def _episodes(db: Session) -> list[Episode]:
    return list(db.scalars(select(Episode).order_by(Episode.queue_order, Episode.episode_id)))


@router.get("/health")
def health() -> dict:
    return {"ok": True, "service": "puzmania"}


@router.get("/bootstrap")
def bootstrap(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    prefs = load_settings(db, cfg)
    episodes = _episodes(db)
    outs = [episode_out(ep) for ep in episodes]
    qids = queue_ids(episodes)
    next_ep = next((ep for ep in outs if ep.overall == "queued"), None)
    if next_ep is None and qids:
        next_ep = next((ep for ep in outs if ep.id == qids[0]), None)
    today = next((ep for ep in outs if ep.overall == "attention"), None)
    history = [
        history_out(row)
        for row in db.scalars(select(PublishEvent).order_by(PublishEvent.created_at.desc()).limit(50))
    ]
    return {
        "series": {"name": "Puzmania", "show": "Field Notes", "tagline": "One episode a day on YouTube and Instagram."},
        "episodes": [ep.model_dump() for ep in outs],
        "queue": qids,
        "settings": settings_out(prefs).model_dump(),
        "history": [row.model_dump() for row in history],
        "platforms": _platform_cards(prefs, cfg),
        "gdrive": gdrive.status(cfg, db),
        "openai": {
            "configured": cfg.openai_configured(),
            "model": cfg.openai_model,
        },
        "joinUrl": cfg.join_url(),
        "campaigns": [
            campaign_service.campaign_out(row)
            for row in db.scalars(select(Campaign).order_by(Campaign.created_at.desc()))
        ],
        "winners": winner_service.featured_list(db),
        "leaderboard": click_service.leaderboard(db, winner_service.winner_prefs(prefs, cfg)["multiplier"]),
        "clickStats": click_service.click_stats(db),
        "nextIntroDate": winner_service.next_intro_date(
            db, date.today(), winner_service.winner_prefs(prefs, cfg)["interval"]
        ).isoformat(),
        "members": db.query(Member).count(),
        "overview": {
            "next": next_ep.model_dump() if next_ep else None,
            "today": today.model_dump() if today else None,
            "stats": {
                "library": len(outs),
                "queue": len(qids),
                "published": sum(1 for ep in outs if ep.overall == "published"),
                "attention": sum(1 for ep in outs if ep.overall == "attention"),
            },
            "nextRun": next_run_at(prefs).isoformat(),
        },
    }


@router.get("/episodes")
def list_episodes(db: Session = Depends(get_db)) -> list[dict]:
    return [episode_out(ep).model_dump() for ep in _episodes(db)]


@router.get("/episodes/{episode_id}")
def get_episode(episode_id: str, db: Session = Depends(get_db)) -> dict:
    ep = db.get(Episode, episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")
    return episode_out(ep).model_dump()


@router.post("/episodes/scan")
def scan(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    found, removed, drive = library.refresh_library(db, cfg)
    return {"scanned": found, "removed": removed, "gdrive": drive}


@router.post("/gdrive/sync")
def gdrive_sync(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    if not cfg.gdrive_configured():
        raise HTTPException(
            400,
            "Google Drive is not configured. Set GDRIVE_FOLDER_ID and GDRIVE_SERVICE_ACCOUNT_JSON (or GDRIVE_SERVICE_ACCOUNT_FILE).",
        )
    found, removed, drive = library.refresh_library(db, cfg)
    return {"scanned": found, "removed": removed, "gdrive": drive}


@router.get("/queue")
def get_queue(db: Session = Depends(get_db)) -> dict:
    items = queue_service.active_queue(db)
    return {"queue": [ep.episode_id for ep in items], "episodes": [episode_out(ep).model_dump() for ep in items]}


@router.post("/queue/reorder")
def reorder_queue(body: ReorderIn, db: Session = Depends(get_db)) -> dict:
    try:
        ids = queue_service.reorder(db, body.episode_id, body.direction)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"queue": ids}


@router.post("/queue/reset")
def reset_queue(db: Session = Depends(get_db)) -> dict:
    return {"queue": queue_service.reset_order(db)}


@router.post("/episodes/{episode_id}/skip")
def skip_episode(episode_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        ep = queue_service.skip(db, episode_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except OperationalError as exc:
        raise HTTPException(503, "Database is busy. Skip again in a moment.") from exc
    return episode_out(ep).model_dump()


@router.post("/episodes/{episode_id}/retry")
def retry_episode(episode_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        ep = queue_service.retry_failed(db, episode_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return episode_out(ep).model_dump()


@router.post("/episodes/{episode_id}/publish")
def publish_one(episode_id: str, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    try:
        return orchestrator.publish_episode(db, cfg, episode_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except OperationalError as exc:
        raise HTTPException(503, "Database is busy. Publish again in a moment.") from exc


@router.post("/episodes/{episode_id}/publish/start")
def start_publish(episode_id: str, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    ep = db.get(Episode, episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")
    return publish_jobs.start(episode_id, cfg).snapshot()


@router.get("/jobs/{job_id}")
def get_publish_job(job_id: str) -> dict:
    job = publish_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Publish job not found")
    return job.snapshot()


@router.post("/runs/preview")
def preview_run(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    return orchestrator.preview(db, cfg)


@router.post("/runs/start")
def start_run(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    return orchestrator.run_daily(db, cfg)


@router.get("/history")
def history(platform: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(PublishEvent).order_by(PublishEvent.created_at.desc()).limit(100)
    rows = list(db.scalars(stmt))
    if platform and platform != "all":
        rows = [row for row in rows if row.platform == platform]
    return [history_out(row).model_dump() for row in rows]


@router.get("/platforms")
def platforms(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> list[dict]:
    return _platform_cards(load_settings(db, cfg), cfg)


@router.patch("/platforms/{platform_id}")
def patch_platform(platform_id: str, body: PlatformPatch, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    if platform_id not in PLATFORM_META:
        raise HTTPException(404, "Unknown platform")
    prefs = load_settings(db, cfg)
    if body.enabled is not None:
        prefs["platforms"][platform_id]["enabled"] = body.enabled
        prefs = save_settings(db, cfg, {"platforms": prefs["platforms"]})
    return next(card for card in _platform_cards(prefs, cfg) if card["id"] == platform_id)


@router.get("/settings")
def get_settings_route(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    return settings_out(load_settings(db, cfg)).model_dump()


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    data = save_settings(db, cfg, body.model_dump(exclude_none=True))
    reschedule(cfg)
    return settings_out(data).model_dump()


@router.get("/campaigns")
def list_campaigns(db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(Campaign).order_by(Campaign.created_at.desc())))
    return {"campaigns": [campaign_service.campaign_out(row) for row in rows]}


@router.post("/campaigns")
async def create_campaign(
    videos: list[UploadFile] = File(...),
    captions: UploadFile | None = File(None),
    platforms: str = Form("youtube_instagram"),
    start_date: str = Form(...),
    end_date: str = Form(...),
    videos_per_day: int = Form(1),
    loop: str = Form("true"),
    name: str = Form(""),
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
) -> dict:
    clips = [item for item in videos if item and item.filename]
    if not clips:
        raise HTTPException(400, "Select at least one video.")
    try:
        chosen = campaign_service.parse_platforms(platforms)
        start = date.fromisoformat(start_date[:10])
        end = date.fromisoformat(end_date[:10])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    incoming = Path(tempfile.mkdtemp(prefix="puz-add-"))
    saved: list[Path] = []
    try:
        for index, upload in enumerate(clips, start=1):
            suffix = Path(upload.filename or "").suffix.lower()
            if suffix not in campaign_service.VIDEO_SUFFIXES:
                raise HTTPException(400, f"{upload.filename} is not a video we can post.")
            dest = incoming / campaign_service._safe_name(upload.filename or f"video-{index}{suffix}")
            if dest.exists():
                dest = incoming / f"{dest.stem}_{index}{dest.suffix}"
            with dest.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            if dest.stat().st_size < 1000:
                raise HTTPException(400, f"{upload.filename} looks empty.")
            saved.append(dest)

        caption_rows = [{"title": "", "description": ""} for _ in saved]
        if captions and captions.filename:
            raw = (await captions.read()).decode("utf-8", errors="replace")
            caption_rows = campaign_service.parse_caption_file(raw, [path.name for path in saved])

        campaign = campaign_service.create_campaign(
            db,
            cfg,
            name=name,
            platforms=chosen,
            start=start,
            end=end,
            videos_per_day=int(videos_per_day),
            loop=(loop or "true").strip().lower() in {"1", "true", "on", "yes"},
            video_paths=saved,
            captions=caption_rows,
        )
    except HTTPException:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    except ValueError as exc:
        shutil.rmtree(incoming, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc
    finally:
        shutil.rmtree(incoming, ignore_errors=True)

    episodes = [
        episode_out(ep).model_dump()
        for ep in db.scalars(select(Episode).where(Episode.campaign_id == campaign.id).order_by(Episode.queue_order))
    ]
    return {
        "campaign": campaign_service.campaign_out(campaign),
        "episodes": episodes,
        "queued": len(episodes),
    }


@router.get("/winners")
def desk_winners(db: Session = Depends(get_db), cfg: Settings = Depends(get_settings)) -> dict:
    prefs = load_settings(db, cfg)
    cfg_w = winner_service.winner_prefs(prefs, cfg)
    today = date.today()
    return {
        "today": winner_service.featured_out(
            winner_service.featured_for_date(db, today) or winner_service.last_featured(db, before=today)
        ),
        "list": winner_service.featured_list(db, limit=30),
        "leaderboard": click_service.leaderboard(db, cfg_w["multiplier"]),
        "clickStats": click_service.click_stats(db),
        "nextIntroDate": winner_service.next_intro_date(db, today, cfg_w["interval"]).isoformat(),
        "members": db.query(Member).count(),
        "mode": cfg_w["mode"],
        "pointsMultiplier": cfg_w["multiplier"],
        "winnerIntroDays": cfg_w["interval"],
    }


@router.post("/winners/feature")
def feature_winner(
    body: FeatureWinnerIn,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
) -> dict:
    prefs = load_settings(db, cfg)
    cfg_w = winner_service.winner_prefs(prefs, cfg)
    today = date.today()
    show = body.showDate or winner_service.next_intro_date(db, today, cfg_w["interval"])
    existing = winner_service.featured_for_date(db, show)
    if existing and existing.episode_id:
        raise HTTPException(400, "That winner day is already attached to a published video.")
    if existing:
        db.delete(existing)
        db.flush()
    member = db.get(Member, body.memberId) if body.memberId else None
    username = (body.username or (member.username if member else "")).strip()
    if not username:
        raise HTTPException(400, "Pick a member or type a username.")
    row = winner_service.create_featured_winner(
        db,
        prefs,
        show,
        username=username,
        points=body.points,
        member_id=member.id if member else None,
        source="manual" if member or body.username else None,
        settings=cfg,
    )
    return winner_service.featured_out(row)


@router.post("/desktop-shortcut")
def desktop_shortcut(cfg: Settings = Depends(get_settings)) -> dict:
    try:
        return desktop_service.create_desktop_shortcut(cfg)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/auth/youtube/start")
def youtube_auth_start(request: Request, cfg: Settings = Depends(get_settings)):
    try:
        return RedirectResponse(
            youtube_oauth.authorization_url(cfg, request_base=str(request.base_url).rstrip("/")),
            status_code=302,
        )
    except ValueError as exc:
        return HTMLResponse(_oauth_page("YouTube is not ready", str(exc), retry="/api/auth/youtube/start"), status_code=400)


@router.get("/auth/youtube/callback")
def youtube_auth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    cfg: Settings = Depends(get_settings),
):
    if error:
        return HTMLResponse(_oauth_page("YouTube authorization cancelled", error, retry="/api/auth/youtube/start"), status_code=400)
    if not code:
        raise HTTPException(400, "Missing authorization code")
    try:
        youtube_oauth.exchange_code(cfg, code, state)
    except Exception as exc:
        return HTMLResponse(_oauth_page("YouTube connection failed", str(exc), retry="/api/auth/youtube/start"), status_code=400)
    return HTMLResponse(_oauth_page("YouTube connected", "Refresh token saved. You can close this tab and return to Puzmania.", success=True))


@router.post("/auth/youtube/disconnect")
def youtube_auth_disconnect() -> dict:
    youtube_oauth.disconnect()
    return {"connected": False}


@router.get("/auth/tiktok/start")
def tiktok_auth_start(cfg: Settings = Depends(get_settings)):
    try:
        return RedirectResponse(tiktok_oauth.authorization_url(cfg), status_code=302)
    except ValueError as exc:
        return HTMLResponse(_oauth_page("TikTok is not ready", str(exc), retry="/api/auth/tiktok/start"), status_code=400)


@router.get("/auth/tiktok/callback")
def tiktok_auth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    cfg: Settings = Depends(get_settings),
):
    if error:
        detail = error_description or error
        return HTMLResponse(_oauth_page("TikTok authorization cancelled", detail, retry="/api/auth/tiktok/start"), status_code=400)
    if not code:
        raise HTTPException(400, "Missing authorization code")
    try:
        tokens = tiktok_oauth.exchange_code(cfg, code, state)
        name = tiktok_oauth.display_name(tokens["access_token"])
    except Exception as exc:
        return HTMLResponse(_oauth_page("TikTok connection failed", str(exc), retry="/api/auth/tiktok/start"), status_code=400)
    who = f" Connected as {name}." if name else ""
    return HTMLResponse(
        _oauth_page(
            "TikTok connected",
            f"Tokens saved.{who} You can close this tab and return to Puzmania.",
            success=True,
        )
    )


@router.post("/auth/tiktok/disconnect")
def tiktok_auth_disconnect(cfg: Settings = Depends(get_settings)) -> dict:
    tiktok_oauth.disconnect(cfg)
    return {"connected": False}


def _oauth_page(title: str, detail: str, success: bool = False, retry: str = "/#platforms") -> str:
    dest = "/#platforms"
    action = (
        f'<a href="{dest}">Back to Platforms</a>'
        if success
        else f'<a href="{retry}">Try again</a> · <a href="{dest}">Back</a>'
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
  body {{ font-family: Georgia, serif; background:#0e0d0b; color:#f3eee4; display:grid; place-items:center; min-height:100vh; margin:0; }}
  main {{ max-width: 28rem; }}
  h1 {{ font-weight:600; }}
  p {{ color:#c9c2b4; line-height:1.5; }}
  a {{ color:#d4a056; }}
</style></head>
<body><main><h1>{escape(title)}</h1><p>{escape(detail)}</p><p>{action}</p></main>
<script>if ({str(success).lower()}) setTimeout(function(){{ location.href = "{dest}"; }}, 1200);</script>
</body></html>"""


def _platform_cards(prefs: dict, cfg: Settings) -> list[dict]:
    cards = []
    for key, meta in PLATFORM_META.items():
        state = prefs.get("platforms", {}).get(key, {})
        cards.append(
            {
                "id": key,
                **meta,
                "enabled": state.get("enabled", True),
                "connected": PUBLISHERS[key].is_configured(cfg),
            }
        )
    return cards
