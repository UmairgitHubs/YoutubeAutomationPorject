from __future__ import annotations

import logging
from pathlib import Path

import httpx

log = logging.getLogger("puzmania.media_stage")

_TIMEOUT = httpx.Timeout(300.0)
_HEADERS = {"User-Agent": "Puzmania/1.0"}


def stage_public_video(path: Path) -> str:
    """Upload a local video and return an HTTPS URL Instagram can fetch."""
    errors: list[str] = []
    for uploader in (_uguu, _x0_at, _catbox, _litterbox, _tmpfiles, _zero_x_zero):
        try:
            url = uploader(path)
        except Exception as exc:
            errors.append(f"{uploader.__name__}: {exc}")
            continue
        if not url.startswith("https://"):
            errors.append(f"{uploader.__name__}: unexpected URL {url[:80]!r}")
            continue
        try:
            _assert_direct_mp4(url)
        except Exception as exc:
            errors.append(f"{uploader.__name__}: {exc}")
            continue
        log.info("Staged %s via %s", path.name, uploader.__name__)
        return url
    raise ValueError("Could not create a public video URL. " + " | ".join(errors[:4]))


def _assert_direct_mp4(url: str) -> None:
    with httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "facebookexternalhit/1.1"}) as client:
        response = client.get(url, headers={"Range": "bytes=0-32"})
    if b"ftyp" not in response.content[:40]:
        ctype = response.headers.get("content-type", "unknown")
        raise ValueError(f"URL is not a direct mp4 ({ctype})")


def _uguu(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post(
            "https://uguu.se/upload",
            files={"files[]": (path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    payload = response.json()
    files = payload.get("files") if isinstance(payload, dict) else None
    url = str((files[0] or {}).get("url") or "") if files else ""
    if not url.startswith("https://"):
        raise ValueError(str(payload)[:200])
    return url


def _x0_at(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post("https://x0.at", files={"file": (path.name, handle, "video/mp4")})
    response.raise_for_status()
    url = response.text.strip()
    if not url.startswith("https://"):
        raise ValueError(url[:200] or response.status_code)
    return url


def _catbox(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    url = response.text.strip()
    if not url.startswith("https://files.catbox.moe/"):
        raise ValueError(url[:200] or response.status_code)
    return url


def _litterbox(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": "12h"},
            files={"fileToUpload": (path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    url = response.text.strip()
    if not url.startswith("https://litter.catbox.moe/"):
        raise ValueError(url[:200] or response.status_code)
    return url


def _zero_x_zero(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post(
            "https://0x0.st",
            files={"file": (path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    url = response.text.strip()
    if not url.startswith("https://"):
        raise ValueError(url[:200] or response.status_code)
    return url


def _tmpfiles(path: Path) -> str:
    with path.open("rb") as handle, httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
        response = client.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (path.name, handle, "video/mp4")},
        )
    response.raise_for_status()
    payload = response.json()
    url = str((payload.get("data") or {}).get("url") or "").replace("http://", "https://", 1)
    if "://tmpfiles.org/dl/" not in url:
        url = url.replace("://tmpfiles.org/", "://tmpfiles.org/dl/", 1)
    if not url.startswith("https://"):
        raise ValueError(str(payload)[:200])
    return url
