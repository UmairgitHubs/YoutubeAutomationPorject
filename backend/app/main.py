from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.join import router as join_router
from app.api.studio import router as studio_router
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.migrate import migrate_schema
from app.seed import seed_if_empty
from app.services.channel import seed_channel_puzzles
from app.services.join import seed_puzzles
from app.services.library import refresh_library
from app.services.scheduler import start_scheduler
from app.services.studio_auth import COOKIE_NAME, session_ok

log = logging.getLogger("puzmania")


def _refresh_in_background(settings) -> None:
    """Download Drive videos without blocking the desk from opening."""
    db = SessionLocal()
    try:
        refresh_library(db, settings)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Startup library sync failed")
    finally:
        db.close()


def _configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "puzmania.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_dir)
    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
        seed_puzzles(db)
        seed_channel_puzzles(db)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Startup seed/scan failed")
        raise
    finally:
        db.close()
    start_scheduler(settings)
    threading.Thread(
        target=_refresh_in_background,
        args=(settings,),
        name="gdrive-initial-sync",
        daemon=True,
    ).start()
    log.info("Puzmania backend ready (dry_run=%s)", settings.dry_run)
    yield


app = FastAPI(title="Puzmania Publishing API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(studio_router)
app.include_router(router)
app.include_router(join_router)

frontend = get_settings().frontend_dir
if frontend.exists():
    @app.get("/")
    async def index() -> FileResponse:
        landing = frontend / "landing.html"
        if landing.is_file():
            return FileResponse(landing)
        return FileResponse(frontend / "index.html")

    @app.get("/desk", response_model=None)
    async def desk(request: Request) -> FileResponse | RedirectResponse:
        if not session_ok(request.cookies.get(COOKIE_NAME)):
            return RedirectResponse("/#studio-login", status_code=302)
        return FileResponse(frontend / "index.html")

    join_dir = frontend / "join"
    if join_dir.exists():
        @app.get("/join")
        @app.get("/join/")
        async def join_home() -> FileResponse:
            return FileResponse(join_dir / "index.html")

        app.mount("/join/css", StaticFiles(directory=str(join_dir / "css")), name="join-css")
        app.mount("/join/js", StaticFiles(directory=str(join_dir / "js")), name="join-js")

    app.mount("/css", StaticFiles(directory=str(frontend / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(frontend / "js")), name="js")
    pics = frontend / "resimler_aa"
    if pics.is_dir():
        app.mount("/resimler_aa", StaticFiles(directory=str(pics)), name="resimler")
