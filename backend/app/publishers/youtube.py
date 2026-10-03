from __future__ import annotations

import logging

from app.config import Settings
from app.models import Episode
from app.publishers.base import ProgressFn, PublishResult, Publisher, emit_progress
from app.services.envfile import upsert_env

log = logging.getLogger("puzmania.youtube")

RECONNECT_MSG = (
    "YouTube login expired (invalid_grant). Open Platforms and click Reconnect channel, "
    "then publish again. If the Google Cloud app is still in Testing, refresh tokens expire after 7 days."
)


class YouTubePublisher(Publisher):
    name = "youtube"

    def is_configured(self, settings: Settings) -> bool:
        return settings.youtube_connected()

    def publish(
        self,
        episode: Episode,
        settings: Settings,
        dry_run: bool,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        if dry_run:
            emit_progress(on_progress, "youtube", "Dry-run — YouTube was not called.", 100, "success")
            return PublishResult(success=True, post_id=f"dry_yt_{episode.episode_id}", dry_run=True)

        if not self.is_configured(settings):
            return PublishResult(success=False, error="YouTube OAuth is not configured (client id, secret, refresh token).")

        video = self.video_path(episode)
        if video is None:
            return PublishResult(success=False, error="Video file is missing from the episode folder.")

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            return PublishResult(success=False, error=f"Google client libraries are not installed: {exc}")

        title = (getattr(episode, "youtube_title", None) or episode.title or "")[:100]
        description = getattr(episode, "youtube_description", None) or episode.description or title
        creds = Credentials(
            token=None,
            refresh_token=settings.youtube_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.youtube_client_id,
            client_secret=settings.youtube_client_secret,
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube",
            ],
        )
        try:
            from google.auth.transport.requests import Request

            emit_progress(on_progress, "youtube", "Refreshing the YouTube login…", 2)
            creds.refresh(Request())
            if creds.refresh_token and creds.refresh_token != settings.youtube_refresh_token:
                upsert_env("YOUTUBE_REFRESH_TOKEN", creds.refresh_token)
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": _tags(episode),
                },
                "status": {"privacyStatus": settings.youtube_privacy or "unlisted"},
            }
            media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True, chunksize=1024 * 1024)
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            emit_progress(on_progress, "youtube", "Uploading to YouTube…", 1)
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = max(1, min(99, int(status.progress() * 100)))
                    emit_progress(on_progress, "youtube", f"Uploading to YouTube… {pct}%", pct)
            video_id = response.get("id") if isinstance(response, dict) else None
            if not video_id:
                return PublishResult(success=False, error="YouTube upload returned no video id.")

            emit_progress(on_progress, "youtube", "Setting the YouTube thumbnail…", 99)
            if episode.thumbnail_path:
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(episode.thumbnail_path, mimetype="image/jpeg"),
                    ).execute()
                except Exception as exc:
                    log.warning("Thumbnail upload failed for %s: %s", episode.episode_id, exc)

            emit_progress(on_progress, "youtube", f"Posted on YouTube · {video_id}", 100, "success")
            return PublishResult(success=True, post_id=video_id)
        except Exception as exc:
            log.exception("YouTube upload failed for %s", episode.episode_id)
            return PublishResult(success=False, error=_friendly_youtube_error(exc))


def _friendly_youtube_error(exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()
    if "invalid_grant" in lower:
        return RECONNECT_MSG
    if "access_denied" in lower or "insufficient" in lower and "scope" in lower:
        return "YouTube permission is missing. Reconnect the channel and accept upload access."
    if "quota" in lower:
        return "YouTube API quota is exhausted for today. Try again tomorrow."
    return (f"YouTube upload failed: {exc}")[:400]


def _tags(episode: Episode) -> list[str]:
    import json

    try:
        tags = json.loads(episode.tags_hashtags or "[]")
        return [str(t).lstrip("#") for t in tags][:30]
    except json.JSONDecodeError:
        return []
