from __future__ import annotations

import logging
import time

import httpx

from app.config import Settings
from app.models import Episode
from app.publishers.base import ProgressFn, PublishResult, Publisher, emit_progress
from app.services.media_stage import stage_public_video
from app.services.reel_prepare import prepare_instagram_reel

log = logging.getLogger("puzmania.instagram")

API_VERSION = "v21.0"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{API_VERSION}"
PROCESS_ATTEMPTS = 60
PROCESS_WAIT_SECONDS = 5


def _graph_base(token: str) -> str:
    # Instagram Login tokens start with IGAA and must use graph.instagram.com.
    if token.startswith("IGAA"):
        return f"https://graph.instagram.com/{API_VERSION}"
    return f"https://graph.facebook.com/{API_VERSION}"


def _graph_error(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if payload.get("message"):
            return str(payload["message"])
    return fallback


class InstagramPublisher(Publisher):
    name = "instagram"

    def is_configured(self, settings: Settings) -> bool:
        return settings.instagram_connected()

    def publish(
        self,
        episode: Episode,
        settings: Settings,
        dry_run: bool,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        if dry_run:
            emit_progress(on_progress, "instagram", "Dry-run — Instagram was not called.", 100, "success")
            return PublishResult(success=True, post_id=f"dry_ig_{episode.episode_id}", dry_run=True)

        if not self.is_configured(settings):
            return PublishResult(success=False, error="Instagram token or IG user id is missing.")

        video = self.video_path(episode)
        if video is None:
            return PublishResult(success=False, error="Video file is missing from the episode folder.")

        emit_progress(on_progress, "instagram", "Preparing a full-frame 9:16 reel…", 8)
        try:
            video = prepare_instagram_reel(video, episode.episode_id)
        except ValueError as exc:
            return PublishResult(success=False, error=f"Instagram video prep failed: {exc}")

        caption = (getattr(episode, "instagram_caption", None) or episode.description or episode.title)[:2200]
        token = settings.instagram_access_token
        user_id = settings.instagram_ig_user_id
        graph = _graph_base(token)
        timeout = httpx.Timeout(300.0)

        try:
            if token.startswith("IGAA"):
                emit_progress(on_progress, "instagram", "Hosting the video so Instagram can fetch it…", 18)
                try:
                    video_url = stage_public_video(video)
                except ValueError as exc:
                    return PublishResult(success=False, error=f"Instagram could not host the local video: {exc}")
                return self._publish_from_url(
                    graph, token, user_id, caption, video_url, timeout, episode.episode_id, on_progress
                )
            emit_progress(on_progress, "instagram", "Uploading the reel to Instagram…", 20)
            return self._publish_resumable(
                graph, token, user_id, caption, video, timeout, episode.episode_id, on_progress
            )
        except httpx.HTTPError as exc:
            return PublishResult(success=False, error=f"Instagram request failed: {exc}")

    def _publish_from_url(
        self,
        graph: str,
        token: str,
        user_id: str,
        caption: str,
        video_url: str,
        timeout: httpx.Timeout,
        episode_id: str,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        with httpx.Client(timeout=timeout) as client:
            emit_progress(on_progress, "instagram", "Creating the Instagram Reels container…", 28)
            create = client.post(
                f"{graph}/{user_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": token,
                },
            )
            created = create.json()
            container_id = created.get("id") if isinstance(created, dict) else None
            if create.status_code >= 400 or not container_id:
                return PublishResult(
                    success=False,
                    error=f"Instagram container failed: {_graph_error(created, str(created))}",
                )
            return self._wait_and_publish(client, graph, token, user_id, container_id, episode_id, on_progress)

    def _publish_resumable(
        self,
        graph: str,
        token: str,
        user_id: str,
        caption: str,
        video,
        timeout: httpx.Timeout,
        episode_id: str,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        file_size = video.stat().st_size
        with httpx.Client(timeout=timeout) as client:
            emit_progress(on_progress, "instagram", "Creating the Instagram Reels container…", 24)
            create = client.post(
                f"{graph}/{user_id}/media",
                data={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": token,
                },
            )
            created = create.json()
            container_id = created.get("id") if isinstance(created, dict) else None
            if create.status_code >= 400 or not container_id:
                return PublishResult(
                    success=False,
                    error=f"Instagram container failed: {_graph_error(created, str(created))}",
                )

            upload_url = created.get("uri") or created.get("upload_url") or f"{RUPLOAD}/{container_id}"
            emit_progress(on_progress, "instagram", "Uploading the reel file…", 40)
            with video.open("rb") as handle:
                upload = client.post(
                    upload_url,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream",
                    },
                    content=handle,
                )
            if upload.status_code >= 400:
                try:
                    uploaded = upload.json()
                except ValueError:
                    uploaded = {"message": upload.text[:400]}
                return PublishResult(
                    success=False,
                    error=f"Instagram upload failed: {_graph_error(uploaded, str(uploaded))}",
                )
            return self._wait_and_publish(client, graph, token, user_id, container_id, episode_id, on_progress)

    def _wait_and_publish(
        self,
        client: httpx.Client,
        graph: str,
        token: str,
        user_id: str,
        container_id: str,
        episode_id: str,
        on_progress: ProgressFn | None = None,
    ) -> PublishResult:
        for attempt in range(PROCESS_ATTEMPTS):
            pct = min(90, 55 + int((attempt / PROCESS_ATTEMPTS) * 35))
            emit_progress(on_progress, "instagram", "Instagram is processing the reel…", pct)
            status = client.get(
                f"{graph}/{container_id}",
                params={"fields": "status_code,status", "access_token": token},
            ).json()
            code = status.get("status_code") if isinstance(status, dict) else None
            if code == "FINISHED":
                break
            if code == "ERROR":
                detail = status.get("status") if isinstance(status, dict) else None
                return PublishResult(
                    success=False,
                    error=f"Instagram processing error: {detail or _graph_error(status, str(status))}",
                )
            time.sleep(PROCESS_WAIT_SECONDS)
        else:
            return PublishResult(success=False, error="Instagram container did not become ready in time.")

        emit_progress(on_progress, "instagram", "Publishing the reel to Instagram…", 94)
        publish = client.post(
            f"{graph}/{user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
        )
        published = publish.json()
        media_id = published.get("id") if isinstance(published, dict) else None
        if publish.status_code >= 400 or not media_id:
            return PublishResult(
                success=False,
                error=f"Instagram publish failed: {_graph_error(published, str(published))}",
            )
        log.info("Published Instagram reel for %s as %s", episode_id, media_id)
        emit_progress(on_progress, "instagram", f"Posted on Instagram · {media_id}", 100, "success")
        return PublishResult(success=True, post_id=media_id)
