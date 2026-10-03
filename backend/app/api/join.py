from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import Member, PuzzleRun
from app.services import channel as channel_service
from app.services import clicks as click_service
from app.services import join as join_service
from app.services import serialize as serialize_service
from app.services import winners as winner_service

router = APIRouter(prefix="/api/join", tags=["join"])


class RegisterIn(BaseModel):
    username: str
    age: int
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class SubmitIn(BaseModel):
    answers: list[str] = Field(default_factory=list)
    elapsedMs: int = 0


class ChannelAnswerIn(BaseModel):
    choice: str = ""


def _ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _set_session(response: Response, member_id: int) -> None:
    response.set_cookie(
        join_service.COOKIE_NAME,
        join_service.make_session_token(member_id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _set_visitor(response: Response, token: str) -> None:
    response.set_cookie(
        click_service.VISITOR_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
        path="/",
    )


def visitor_token(request: Request, response: Response) -> str:
    token = (request.cookies.get(click_service.VISITOR_COOKIE) or "").strip()
    if len(token) < 8:
        token = click_service.new_visitor_token()
    _set_visitor(response, token)
    return token


def current_member(request: Request, db: Session = Depends(get_db)) -> Member:
    member_id = join_service.member_id_from_token(request.cookies.get(join_service.COOKIE_NAME))
    if not member_id:
        raise HTTPException(401, "Please join or log in first.")
    member = db.get(Member, member_id)
    if not member:
        raise HTTPException(401, "Please join or log in first.")
    return member


def optional_member(request: Request, db: Session = Depends(get_db)) -> Member | None:
    member_id = join_service.member_id_from_token(request.cookies.get(join_service.COOKIE_NAME))
    if not member_id:
        return None
    return db.get(Member, member_id)


def _multiplier(db: Session, cfg: Settings) -> int:
    prefs = serialize_service.load_settings(db, cfg)
    return winner_service.winner_prefs(prefs, cfg)["multiplier"]


def _me(db: Session, member: Member | None, cfg: Settings) -> dict | None:
    if not member:
        return None
    return winner_service.member_public(db, member, _multiplier(db, cfg))


def _catch_click(
    db: Session,
    request: Request,
    response: Response,
    member: Member | None,
    src: str | None,
    ep: str | None,
) -> dict:
    token = visitor_token(request, response)
    platform = click_service.normalize_platform(src) or click_service.normalize_platform(request.query_params.get("from"))
    episode_id = (ep or request.query_params.get("ep") or "").strip()
    caught = False
    if platform:
        row = click_service.record_click(
            db,
            visitor_token=token,
            platform=platform,
            episode_id=episode_id,
            member_id=member.id if member else None,
        )
        caught = row is not None
        if member:
            click_service.attach_visitor(db, member, token)
    elif member:
        click_service.attach_visitor(db, member, token)
    return {"visitor": token, "caught": caught, "platform": platform, "episodeId": (episode_id or "").upper()}


@router.get("/bootstrap")
def join_bootstrap(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
    member: Member | None = Depends(optional_member),
    src: str | None = Query(default=None),
    ep: str | None = Query(default=None),
) -> dict:
    today = date.today()
    prefs = serialize_service.load_settings(db, cfg)
    cfg_w = winner_service.winner_prefs(prefs, cfg)
    click = _catch_click(db, request, response, member, src, ep)
    geo = join_service.lookup_geo(_ip(request))
    featured = winner_service.last_featured(db, before=today) or winner_service.featured_for_date(db, today)
    played = False
    if member:
        played = (
            db.scalar(
                select(PuzzleRun).where(
                    PuzzleRun.member_id == member.id,
                    PuzzleRun.play_date == today,
                    PuzzleRun.finished_at.is_not(None),
                )
            )
            is not None
        )
    return {
        "siteUrl": cfg.join_url(),
        "minAge": cfg.join_min_age,
        "runSeconds": join_service.RUN_SECONDS,
        "me": _me(db, member, cfg),
        "geo": geo,
        "caughtFromShort": click["caught"],
        "todayWinner": winner_service.featured_out(featured),
        "winners": winner_service.featured_list(db),
        "puzzleWinner": join_service.winner_out(join_service.winner_for(db, today)),
        "playedToday": played,
        "pointsMultiplier": cfg_w["multiplier"],
        "winnerMode": cfg_w["mode"],
        "channelPoints": int(member.channel_points or 0) if member else 0,
        "channelTimer": channel_service.TIMER_START,
        "channelFactor": channel_service.POINTS_FACTOR,
    }


@router.post("/signup")
def signup(
    body: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
    src: str | None = Query(default=None),
    ep: str | None = Query(default=None),
) -> dict:
    geo = join_service.lookup_geo(_ip(request))
    try:
        member = join_service.register_member(
            db,
            cfg,
            username=body.username,
            age=body.age,
            password=body.password,
            country=geo.get("country"),
            region=geo.get("region"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_session(response, member.id)
    click = _catch_click(db, request, response, member, src, ep)
    return {"me": _me(db, member, cfg), "geo": geo, "caughtFromShort": click["caught"]}


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
    src: str | None = Query(default=None),
    ep: str | None = Query(default=None),
) -> dict:
    try:
        member = join_service.login_member(db, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _set_session(response, member.id)
    _catch_click(db, request, response, member, src, ep)
    return {"me": _me(db, member, cfg)}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(join_service.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
    member: Member = Depends(current_member),
) -> dict:
    return _me(db, member, cfg)


@router.get("/puzzles")
def puzzles(
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> dict:
    today = date.today()
    try:
        run = join_service.start_run(db, member, today)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    items = join_service.todays_puzzles(db, today)
    return {
        "runId": run.id,
        "seconds": join_service.RUN_SECONDS,
        "puzzles": [join_service.puzzle_public(row) for row in items],
    }


@router.post("/puzzles/submit")
def submit(
    body: SubmitIn,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> dict:
    try:
        return join_service.submit_run(db, member, date.today(), body.answers, body.elapsedMs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/channel")
def channel_home(
    db: Session = Depends(get_db),
    member: Member | None = Depends(optional_member),
) -> dict:
    channel_service.seed_channel_puzzles(db)
    return {
        "timerStart": channel_service.TIMER_START,
        "factor": channel_service.POINTS_FACTOR,
        "channelPoints": int(member.channel_points or 0) if member else 0,
        "puzzles": channel_service.catalog(db, member),
    }


@router.post("/channel/puzzles/{puzzle_id}/start")
def channel_start(
    puzzle_id: int,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> dict:
    try:
        return channel_service.start_attempt(db, member, puzzle_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/channel/puzzles/{puzzle_id}/answer")
def channel_answer(
    puzzle_id: int,
    body: ChannelAnswerIn,
    db: Session = Depends(get_db),
    cfg: Settings = Depends(get_settings),
    member: Member = Depends(current_member),
) -> dict:
    try:
        result = channel_service.submit_answer(db, member, puzzle_id, body.choice)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result["me"] = _me(db, member, cfg)
    return result


@router.get("/channel/video/{episode_id}")
def channel_video(
    episode_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(current_member),
) -> FileResponse:
    path = channel_service.video_file(episode_id, db)
    if path is None:
        raise HTTPException(404, "That video is not on the channel.")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/winners")
def winners(db: Session = Depends(get_db)) -> dict:
    today = date.today()
    featured = winner_service.last_featured(db, before=today) or winner_service.featured_for_date(db, today)
    return {
        "today": winner_service.featured_out(featured),
        "list": winner_service.featured_list(db),
        "puzzleToday": join_service.winner_out(join_service.winner_for(db, today)),
    }
