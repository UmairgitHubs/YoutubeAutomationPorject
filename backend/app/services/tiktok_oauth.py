from __future__ import annotations

import hashlib
import json
import logging
import secrets
import string
from urllib.parse import urlencode

import httpx

from app.config import PROJECT_ROOT, Settings
from app.services.envfile import upsert_env

log = logging.getLogger("puzmania.tiktok")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_URL = "https://open.tiktokapis.com/v2/user/info/"
SCOPES = ["user.info.basic", "video.publish"]
STATE_PATH = PROJECT_ROOT / "data" / "tiktok_oauth_state.txt"
_PKCE_CHARS = string.ascii_letters + string.digits + "-._~"


def redirect_uri(settings: Settings) -> str:
    if settings.tiktok_redirect_uri:
        return settings.tiktok_redirect_uri.rstrip("/")
    return f"http://{settings.host}:{settings.port}/api/auth/tiktok/callback"


def authorization_url(settings: Settings) -> str:
    if not settings.tiktok_client_key or not settings.tiktok_client_secret:
        uri = redirect_uri(settings)
        raise ValueError(
            "TikTok client key and secret are not set in .env. "
            "Copy them from the puzmaniax app, add Login Kit, then register this exact Redirect URI: "
            + uri
        )
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"state": state, "verifier": verifier}), encoding="utf-8")
    params = {
        "client_key": settings.tiktok_client_key,
        "scope": ",".join(SCOPES),
        "response_type": "code",
        "redirect_uri": redirect_uri(settings),
        "state": state,
        "disable_auto_auth": "1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(settings: Settings, code: str, state: str | None) -> dict:
    saved_state, verifier = _load_oauth_session()
    if saved_state and state and saved_state != state:
        raise ValueError("OAuth state mismatch. Start the TikTok connect flow again.")
    if not verifier:
        raise ValueError("TikTok PKCE verifier is missing. Start the connect flow again from Platforms.")
    payload = {
        "client_key": settings.tiktok_client_key,
        "client_secret": settings.tiktok_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(settings),
        "code_verifier": verifier,
    }
    data = _token_request(payload)
    _persist(data)
    log.info("TikTok tokens stored. Account is ready for Direct Post.")
    return data


def ensure_access_token(settings: Settings) -> str:
    if settings.tiktok_refresh_token and settings.tiktok_client_key and settings.tiktok_client_secret:
        data = _token_request(
            {
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": settings.tiktok_refresh_token,
            }
        )
        _persist(data)
        token = data["access_token"]
        settings.tiktok_access_token = token
        if data.get("refresh_token"):
            settings.tiktok_refresh_token = data["refresh_token"]
        return token
    if settings.tiktok_access_token:
        return settings.tiktok_access_token
    raise ValueError("TikTok is not connected. Use Connect account on the Platforms page.")


def disconnect(settings: Settings | None = None) -> None:
    cfg = settings or Settings()
    if cfg.tiktok_access_token and cfg.tiktok_client_key and cfg.tiktok_client_secret:
        try:
            httpx.post(
                REVOKE_URL,
                data={
                    "client_key": cfg.tiktok_client_key,
                    "client_secret": cfg.tiktok_client_secret,
                    "token": cfg.tiktok_access_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            log.warning("TikTok revoke request failed: %s", exc)
    upsert_env("TIKTOK_ACCESS_TOKEN", "")
    upsert_env("TIKTOK_REFRESH_TOKEN", "")
    upsert_env("TIKTOK_OPEN_ID", "")
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    log.info("TikTok tokens cleared.")


def display_name(access_token: str) -> str | None:
    try:
        response = httpx.get(
            USER_URL,
            params={"fields": "open_id,display_name"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        body = response.json()
        user = (body.get("data") or {}).get("user") or {}
        name = user.get("display_name")
        return str(name) if name else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def _token_request(payload: dict) -> dict:
    try:
        response = httpx.post(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
            timeout=30.0,
        )
        body = response.json()
    except httpx.HTTPError as exc:
        raise ValueError(f"TikTok token request failed: {exc}") from exc
    except ValueError as exc:
        raise ValueError("TikTok returned a non-JSON token response.") from exc

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    error = body.get("error") or (data.get("error") if isinstance(data, dict) else None)
    if response.status_code >= 400 or (error and error not in ("", "ok", 0)):
        detail = body.get("error_description") or body.get("message")
        if isinstance(error, dict):
            detail = error.get("message") or detail
            error = error.get("code") or error.get("error")
        raise ValueError(detail or f"TikTok token error: {error}")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ValueError("TikTok did not return an access token. Check Login Kit scopes and the redirect URI.")
    return data


def _persist(data: dict) -> None:
    upsert_env("TIKTOK_ACCESS_TOKEN", data["access_token"])
    if data.get("refresh_token"):
        upsert_env("TIKTOK_REFRESH_TOKEN", data["refresh_token"])
    if data.get("open_id"):
        upsert_env("TIKTOK_OPEN_ID", data["open_id"])


def _pkce_pair() -> tuple[str, str]:
    verifier = "".join(secrets.choice(_PKCE_CHARS) for _ in range(64))
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def _load_oauth_session() -> tuple[str, str]:
    if not STATE_PATH.exists():
        return "", ""
    raw = STATE_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, ""
    return str(data.get("state") or ""), str(data.get("verifier") or "")
