"""Fixed studio gate. One username and password, then the publishing desk."""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request

STUDIO_USERNAME = "studio"
STUDIO_PASSWORD = "puzmania"
COOKIE_NAME = "puz_studio"
_MAX_AGE = timedelta(days=14)
_SECRET = b"puzmania-studio-login"


def credentials_match(username: str, password: str) -> bool:
    got_user = hashlib.sha256((username or "").strip().encode("utf-8")).digest()
    expect_user = hashlib.sha256(STUDIO_USERNAME.encode("utf-8")).digest()
    got_pass = hashlib.sha256((password or "").encode("utf-8")).digest()
    expect_pass = hashlib.sha256(STUDIO_PASSWORD.encode("utf-8")).digest()
    return hmac.compare_digest(got_user, expect_user) and hmac.compare_digest(got_pass, expect_pass)


def make_token() -> str:
    stamp = str(int(datetime.now(UTC).timestamp()))
    sig = hmac.new(_SECRET, f"studio.{stamp}".encode("ascii"), hashlib.sha256).hexdigest()
    return f"{stamp}.{sig}"


def session_ok(token: str | None) -> bool:
    if not token or token.count(".") != 1:
        return False
    stamp, sig = token.split(".", 1)
    expect = hmac.new(_SECRET, f"studio.{stamp}".encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return False
    try:
        issued = datetime.fromtimestamp(int(stamp), UTC)
    except ValueError:
        return False
    return datetime.now(UTC) - issued <= _MAX_AGE


def require_studio(request: Request) -> None:
    if not session_ok(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Studio login required.")
