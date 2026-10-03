from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.models import Episode

ProgressFn = Callable[[str, str, int | None, str], None]


def emit_progress(
    on_progress: ProgressFn | None,
    step: str,
    message: str,
    percent: int | None = None,
    status: str = "running",
) -> None:
    if on_progress:
        on_progress(step, message, percent, status)


@dataclass
class PublishResult:
    success: bool
    post_id: str | None = None
    error: str | None = None
    dry_run: bool = False


class Publisher:
    name = "base"

    def is_configured(self, settings: Settings) -> bool:
        return False

    def video_path(self, episode: Episode) -> Path | None:
        if not episode.folder_path:
            return None
        folder = Path(episode.folder_path)
        if episode.filename:
            candidate = folder / episode.filename
            if candidate.exists():
                return candidate
        for pattern in ("*.mp4", "*.mov", "*.m4v"):
            found = next(folder.glob(pattern), None)
            if found:
                return found
        return None

    def publish(
        self,
        episode: Episode,
        settings: Settings,
        dry_run: bool,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        raise NotImplementedError
