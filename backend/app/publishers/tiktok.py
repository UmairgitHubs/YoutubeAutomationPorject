from __future__ import annotations

import logging

import httpx

from app.config import Settings
from app.models import Episode
from app.publishers.base import ProgressFn, PublishResult, Publisher, emit_progress
from app.services import tiktok_oauth

log = logging.getLogger("puzmania.tiktok")
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


class TikTokPublisher(Publisher):
    name = "tiktok"

    def is_configured(self, settings: Settings) -> bool:
        return settings.tiktok_connected()

    def publish(
        self,
        episode: Episode,
        settings: Settings,
        dry_run: bool,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        if dry_run:
            emit_progress(on_progress, "tiktok", "Dry-run — TikTok was not called.", 100, "success")
            return PublishResult(success=True, post_id=f"dry_tt_{episode.episode_id}", dry_run=True)

        if not self.is_configured(settings):
            return PublishResult(
                success=False,
                error="TikTok is not connected. Use Connect account on the Platforms page.",
            )

        try:
            access_token = tiktok_oauth.ensure_access_token(settings)
        except ValueError as exc:
            return PublishResult(success=False, error=str(exc))

        video = self.video_path(episode)
        if video is None:
            return PublishResult(success=False, error="Video file is missing from the episode folder.")

        size = video.stat().st_size
        caption = (getattr(episode, "tiktok_caption", None) or episode.title or "")[:150]
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }
        payload = {
            "post_info": {
                "title": caption[:150],
                "privacy_level": "SELF_ONLY",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        }

        try:
            emit_progress(on_progress, "tiktok", "Starting the TikTok upload…", 10)
            with httpx.Client(timeout=120.0) as client:
                init = client.post(INIT_URL, headers=headers, json=payload)
                data = init.json()
                if init.status_code >= 400 or data.get("error", {}).get("code") not in (None, "ok"):
                    message = data.get("error", {}).get("message") or init.text
                    return PublishResult(success=False, error=f"TikTok init failed: {message}")

                upload_url = data.get("data", {}).get("upload_url")
                publish_id = data.get("data", {}).get("publish_id")
                if not upload_url:
                    return PublishResult(success=False, error="TikTok did not return an upload URL.")

                emit_progress(on_progress, "tiktok", "Uploading the video to TikTok…", 40)
                video_bytes = video.read_bytes()
                upload = client.put(
                    upload_url,
                    content=video_bytes,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(size),
                        "Content-Range": f"bytes 0-{size - 1}/{size}",
                    },
                )
                if upload.status_code >= 400:
                    return PublishResult(success=False, error=f"TikTok upload failed: {upload.text[:300]}")
        except httpx.HTTPError as exc:
            return PublishResult(success=False, error=f"TikTok request failed: {exc}")

        post_id = publish_id or f"tt_{episode.episode_id}"
        emit_progress(on_progress, "tiktok", f"Posted on TikTok · {post_id}", 100, "success")
        return PublishResult(success=True, post_id=post_id)
