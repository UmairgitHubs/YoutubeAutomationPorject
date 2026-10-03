from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.database import SessionLocal
from app.services.library import refresh_library
from app.services.orchestrator import run_daily
from app.services.serialize import load_settings

log = logging.getLogger("puzmania.scheduler")
_scheduler: BackgroundScheduler | None = None


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone=settings.timezone)
    _apply_jobs(_scheduler, settings)
    _scheduler.start()
    log.info("Scheduler started")
    return _scheduler


def reschedule(settings: Settings) -> None:
    if not _scheduler:
        return
    _apply_jobs(_scheduler, settings)
    log.info("Scheduler jobs refreshed")


def _apply_jobs(scheduler: BackgroundScheduler, settings: Settings) -> None:
    db = SessionLocal()
    try:
        prefs = load_settings(db, settings)
    finally:
        db.close()

    hour, minute = (prefs.get("publishTime") or "09:00").split(":")
    tz = prefs.get("timezone") or settings.timezone
    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        id="daily_publish",
        replace_existing=True,
        kwargs={"settings": settings},
    )
    scheduler.add_job(
        _scan_job,
        CronTrigger(minute="*/15", timezone=tz),
        id="folder_scan",
        replace_existing=True,
        kwargs={"settings": settings},
    )


def _daily_job(settings: Settings) -> None:
    db = SessionLocal()
    try:
        refresh_library(db, settings)
        result = run_daily(db, settings)
        db.commit()
        log.info("Daily run complete: %s", result.get("message"))
    except Exception:
        db.rollback()
        log.exception("Daily run failed")
    finally:
        db.close()


def _scan_job(settings: Settings) -> None:
    db = SessionLocal()
    try:
        refresh_library(db, settings)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Folder scan failed")
    finally:
        db.close()
