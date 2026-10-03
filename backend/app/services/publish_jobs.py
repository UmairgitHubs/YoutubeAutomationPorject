from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.database import SessionLocal
from app.services.orchestrator import publish_episode

log = logging.getLogger("puzmania.publish_jobs")

STEP_ORDER = ("caption", "youtube", "instagram", "tiktok")
STEP_LABELS = {
    "caption": "Captions",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}
STEP_WEIGHT = {"caption": 10, "youtube": 30, "instagram": 30, "tiktok": 30}
JOB_TTL_SECONDS = 3600

_lock = threading.Lock()
_JOBS: dict[str, PublishJob] = {}
_BY_EPISODE: dict[str, str] = {}


def _empty_steps() -> dict[str, dict]:
    return {
        key: {"id": key, "label": STEP_LABELS[key], "status": "pending", "detail": "", "percent": None}
        for key in STEP_ORDER
    }


@dataclass
class PublishJob:
    id: str
    episode_id: str
    state: str = "running"
    percent: int = 0
    message: str = "Starting…"
    steps: dict[str, dict] = field(default_factory=_empty_steps)
    result: dict | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)

    def emit(self, step: str, message: str, percent: int | None = None, status: str = "running") -> None:
        with _lock:
            row = self.steps.setdefault(
                step,
                {"id": step, "label": STEP_LABELS.get(step, step.title()), "status": "pending", "detail": "", "percent": None},
            )
            row["status"] = status
            row["detail"] = message
            if percent is not None:
                row["percent"] = max(0, min(100, int(percent)))
            elif status in {"success", "skipped", "error"}:
                row["percent"] = 100 if status != "error" else row.get("percent") or 0
            self.message = message
            self.percent = _overall_percent(self.steps)
            self.updated_at = time.time()

    def finish(self, result: dict) -> None:
        with _lock:
            self.result = result
            overall = result.get("overall")
            self.state = "error" if overall == "attention" else "success"
            if self.state == "success":
                self.percent = 100
                self.message = result.get("summary") or "Publish finished."
            else:
                self.error = result.get("summary") or "One or more platforms failed."
                self.message = self.error
            self.updated_at = time.time()

    def fail(self, error: str) -> None:
        with _lock:
            self.state = "error"
            self.error = error
            self.message = error
            self.updated_at = time.time()

    def snapshot(self) -> dict:
        with _lock:
            return {
                "id": self.id,
                "episodeId": self.episode_id,
                "state": self.state,
                "percent": self.percent,
                "message": self.message,
                "steps": [dict(self.steps[key]) for key in STEP_ORDER if key in self.steps],
                "result": self.result,
                "error": self.error,
            }


def start(episode_id: str, settings: Settings) -> PublishJob:
    _gc()
    with _lock:
        existing_id = _BY_EPISODE.get(episode_id)
        existing = _JOBS.get(existing_id or "")
        if existing and existing.state == "running":
            return existing
        job = PublishJob(id=uuid.uuid4().hex, episode_id=episode_id)
        _JOBS[job.id] = job
        _BY_EPISODE[episode_id] = job.id
    threading.Thread(target=_run, args=(job, settings), name=f"publish-{episode_id}", daemon=True).start()
    return job


def get(job_id: str) -> PublishJob | None:
    with _lock:
        return _JOBS.get(job_id)


def _run(job: PublishJob, settings: Settings) -> None:
    job.emit("caption", "Starting publish…", 0)
    db = SessionLocal()
    try:
        result = publish_episode(db, settings, job.episode_id, on_progress=job.emit)
        db.commit()
        job.finish(result)
    except Exception as exc:
        db.rollback()
        log.exception("Publish job failed for %s", job.episode_id)
        job.fail(str(exc)[:400])
    finally:
        db.close()


def _overall_percent(steps: dict[str, dict]) -> int:
    total = 0.0
    weight_sum = 0
    for key, weight in STEP_WEIGHT.items():
        row = steps.get(key) or {}
        status = row.get("status") or "pending"
        if status == "pending":
            part = 0
        elif status in {"success", "skipped"}:
            part = 100
        elif status == "error":
            part = row.get("percent") or 100
        else:
            part = row.get("percent") if row.get("percent") is not None else 15
        total += weight * (part / 100)
        weight_sum += weight
    if not weight_sum:
        return 0
    return max(0, min(100, int(round(total))))


def _gc() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _lock:
        stale = [job_id for job_id, job in _JOBS.items() if job.updated_at < cutoff]
        for job_id in stale:
            job = _JOBS.pop(job_id, None)
            if job and _BY_EPISODE.get(job.episode_id) == job_id:
                _BY_EPISODE.pop(job.episode_id, None)
