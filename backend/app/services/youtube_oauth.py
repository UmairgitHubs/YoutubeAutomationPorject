from __future__ import annotations

import json
import logging
import os
from google_auth_oauthlib.flow import Flow

from app.config import PROJECT_ROOT, Settings
from app.services.envfile import upsert_env

# Local HTTP callbacks (127.0.0.1 / localhost) are required for this desktop-style flow.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

log = logging.getLogger("puzmania.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
STATE_PATH = PROJECT_ROOT / "data" / "youtube_oauth_state.txt"
CALLBACK_PATH = "/api/auth/youtube/callback"


def redirect_uri(settings: Settings, request_base: str | None = None) -> str:
    if settings.youtube_redirect_uri:
        return settings.youtube_redirect_uri.rstrip("/")
    if request_base:
        return request_base.rstrip("/") + CALLBACK_PATH
    return f"http://{settings.host}:{settings.port}{CALLBACK_PATH}"


def registered_redirects(settings: Settings, chosen: str) -> list[str]:
    uris = [
        f"http://127.0.0.1:{settings.port}{CALLBACK_PATH}",
        f"http://localhost:{settings.port}{CALLBACK_PATH}",
        chosen,
    ]
    seen: list[str] = []
    for uri in uris:
        if uri not in seen:
            seen.append(uri)
    return seen


def _flow(settings: Settings, uri: str) -> Flow:
    if not settings.youtube_client_id or not settings.youtube_client_secret:
        raise ValueError(
            "YouTube client id and secret are not set in .env. "
            f"In Google Cloud, use a Web application OAuth client and add this Authorized redirect URI: {uri}"
        )
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": registered_redirects(settings, uri),
            }
        },
        scopes=SCOPES,
        redirect_uri=uri,
    )


def authorization_url(settings: Settings, request_base: str | None = None) -> str:
    uri = redirect_uri(settings, request_base)
    flow = _flow(settings, uri)
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"state": state, "redirect_uri": uri}), encoding="utf-8")
    return url


def exchange_code(settings: Settings, code: str, state: str | None) -> str:
    saved_state, uri = _load_oauth_session(settings)
    if saved_state and state and saved_state != state:
        raise ValueError("OAuth state mismatch. Start the YouTube connect flow again.")
    flow = _flow(settings, uri)
    flow.fetch_token(code=code)
    refresh = flow.credentials.refresh_token
    if not refresh:
        raise ValueError("Google did not return a refresh token. Revoke app access and connect again with consent.")
    upsert_env("YOUTUBE_REFRESH_TOKEN", refresh)
    log.info("YouTube refresh token stored. Channel is ready for unattended uploads.")
    return refresh


def disconnect() -> None:
    upsert_env("YOUTUBE_REFRESH_TOKEN", "")
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    log.info("YouTube refresh token cleared.")


def _load_oauth_session(settings: Settings) -> tuple[str, str]:
    if not STATE_PATH.exists():
        return "", redirect_uri(settings)
    raw = STATE_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return "", redirect_uri(settings)
    try:
        data = json.loads(raw)
        return str(data.get("state") or ""), str(data.get("redirect_uri") or redirect_uri(settings))
    except json.JSONDecodeError:
        return raw, redirect_uri(settings)
