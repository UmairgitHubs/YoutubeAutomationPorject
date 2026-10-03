"""Join-only site for Shorts viewers — no publishing desk, no scheduler."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.join import router as join_router
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.migrate import migrate_schema
from app.services.channel import seed_channel_puzzles
from app.services.join import seed_puzzles

log = logging.getLogger("puzmania.public_join")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)
    db = SessionLocal()
    try:
        seed_puzzles(db)
        seed_channel_puzzles(db)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Join site seed failed")
        raise
    finally:
        db.close()
    log.info("Public join site ready")
    yield


app = FastAPI(title="Puzmania Join", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(join_router)

frontend = get_settings().frontend_dir
join_dir = frontend / "join"


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/join", status_code=302)


if join_dir.is_dir():
    @app.get("/join")
    @app.get("/join/")
    async def join_home() -> FileResponse:
        return FileResponse(join_dir / "index.html")

    app.mount("/join/css", StaticFiles(directory=str(join_dir / "css")), name="join-css")
    app.mount("/join/js", StaticFiles(directory=str(join_dir / "js")), name="join-js")
