from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.models import AppSetting
from app.services.series import SERIES_PREFIX, episode_number, series_from_folder

log = logging.getLogger("puzmania.gdrive")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"
KEEP_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".json", ".jpg", ".jpeg", ".png"}
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,200}$")
LAST_SYNC_KEY = "gdrive_last_sync"


def status(settings: Settings, db=None) -> dict:
    last = None
    if db is not None:
        row = db.get(AppSetting, LAST_SYNC_KEY)
        if row and row.value:
            try:
                last = json.loads(row.value)
            except json.JSONDecodeError:
                last = None
    folder = settings.gdrive_folder_id.strip()
    return {
        "configured": settings.gdrive_configured(),
        "folderHint": folder[-8:] if folder else None,
        "lastSync": (last or {}).get("at"),
        "lastResult": (last or {}).get("summary"),
        "hint": (
            "Share Puz_shorts (alien_finals, blur_finals, jig_finals) or ALIEN_01-style packages "
            "with the service account, then set GDRIVE_FOLDER_ID and GDRIVE_SERVICE_ACCOUNT_JSON."
        ),
    }


def sync_episodes(settings: Settings, db=None) -> dict:
    """Download episode packages from Drive into settings.episodes_dir."""
    if not settings.gdrive_configured():
        return {"ok": True, "skipped": True, "reason": "Google Drive is not configured."}

    dest_root = Path(settings.episodes_dir)
    dest_root.mkdir(parents=True, exist_ok=True)

    try:
        service = _drive_service(settings)
        packages = list(_iter_packages(service, settings.gdrive_folder_id.strip()))
    except Exception as exc:
        log.exception("Google Drive listing failed")
        result = {"ok": False, "skipped": False, "error": str(exc)[:300], "downloaded": [], "updated": []}
        _store_sync(db, result)
        return result

    downloaded: list[str] = []
    updated: list[str] = []
    for package in packages:
        local_dir = dest_root / package["name"]
        local_dir.mkdir(parents=True, exist_ok=True)
        for remote in package["files"]:
            local = local_dir / remote["name"]
            if _up_to_date(local, remote):
                continue
            existed = local.exists()
            label = f"{package['name']}/{remote['name']}"
            try:
                _download_file(service, remote["id"], local)
            except Exception as exc:
                log.warning("Drive download failed for %s: %s", label, exc)
                continue
            if existed:
                updated.append(label)
            else:
                downloaded.append(label)
            log.info("Drive synced %s", label)
        if package.get("write_meta"):
            _write_episode_metadata(local_dir, package)

    removed: list[str] = []
    if settings.gdrive_delete_missing and packages:
        remote_names = {pkg["name"] for pkg in packages}
        for folder in list(dest_root.iterdir()):
            if folder.is_dir() and folder.name not in remote_names and PACKAGE_NAME.match(folder.name):
                shutil.rmtree(folder)
                removed.append(folder.name)

    result = {
        "ok": True,
        "skipped": False,
        "packages": len(packages),
        "downloaded": downloaded,
        "updated": updated,
        "removed": removed,
        "summary": _summary(packages, downloaded, updated),
    }
    _store_sync(db, result)
    return result


def _summary(packages: list[dict], downloaded: list[str], updated: list[str]) -> str:
    if not packages:
        return "Drive folder is empty or not shared with the service account."
    if not downloaded and not updated:
        return f"{len(packages)} package(s) already up to date."
    bits = [f"{len(packages)} package(s)"]
    if downloaded:
        bits.append(f"{len(downloaded)} new file(s)")
    if updated:
        bits.append(f"{len(updated)} updated")
    return ", ".join(bits) + "."


def _store_sync(db, result: dict) -> None:
    if db is None:
        return
    payload = json.dumps(
        {
            "at": datetime.now(UTC).isoformat(),
            "summary": result.get("summary") or result.get("error") or result.get("reason"),
            "ok": result.get("ok"),
        }
    )
    row = db.get(AppSetting, LAST_SYNC_KEY)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=LAST_SYNC_KEY, value=payload))


def _drive_service(settings: Settings):
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_credentials(settings), cache_discovery=False)


def _credentials(settings: Settings):
    from google.oauth2.service_account import Credentials

    info = parse_service_account_json(settings.gdrive_service_account_json)
    if info:
        _cache_service_account(settings, info)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    path = settings.gdrive_credentials_path()
    if path is None:
        raise ValueError(
            "Google Drive service account is missing. "
            "Set GDRIVE_SERVICE_ACCOUNT_JSON (Railway) or GDRIVE_SERVICE_ACCOUNT_FILE."
        )
    return Credentials.from_service_account_file(str(path), scopes=SCOPES)


def parse_service_account_json(raw: str) -> dict | None:
    text = (raw or "").strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("GDRIVE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        raise ValueError("GDRIVE_SERVICE_ACCOUNT_JSON must be a Google service-account key.")
    return data


def _cache_service_account(settings: Settings, info: dict) -> None:
    dest = Path(settings.database_path).expanduser().resolve().parent / "gdrive-sa.json"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(info), encoding="utf-8")
    except OSError:
        log.info("Could not cache Drive credentials at %s", dest)


def _iter_packages(service, folder_id: str):
    children = _list_children(service, folder_id)
    series_dirs = _collect_series_dirs(service, children)
    if series_dirs:
        yield from _packages_from_series_dirs(service, series_dirs)
        return
    for item in children:
        name = item.get("name") or ""
        if item.get("mimeType") != FOLDER_MIME or not PACKAGE_NAME.match(name):
            continue
        files = [row for row in _list_children(service, item["id"]) if _wanted_file(row)]
        yield {"name": name, "files": files}


def _collect_series_dirs(service, children: list[dict]) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []
    nested: list[dict] = []
    for item in children:
        if item.get("mimeType") != FOLDER_MIME:
            continue
        name = item.get("name") or ""
        series = series_from_folder(name)
        if series:
            found.append((series, item))
        else:
            nested.append(item)
    if found:
        return found
    for folder in nested:
        for child in _list_children(service, folder["id"]):
            if child.get("mimeType") != FOLDER_MIME:
                continue
            series = series_from_folder(child.get("name") or "")
            if series:
                found.append((series, child))
    return found


def _packages_from_series_dirs(service, series_dirs: list[tuple[str, dict]]):
    seen: set[str] = set()
    for series, folder in series_dirs:
        files = [row for row in _list_children(service, folder["id"]) if _wanted_file(row)]
        videos = [row for row in files if Path(row.get("name") or "").suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}]
        videos.sort(key=lambda row: episode_number(row.get("name") or ""))
        for video in videos:
            number = episode_number(video.get("name") or "")
            episode_id = f"{series}_{number:02d}"
            if episode_id in seen:
                continue
            seen.add(episode_id)
            extras = []
            for row in files:
                if row["id"] == video["id"]:
                    continue
                name = (row.get("name") or "").lower()
                suffix = Path(name).suffix
                if name == "metadata.json" or suffix in {".jpg", ".jpeg", ".png"}:
                    extras.append(row)
            yield {
                "name": episode_id,
                "files": [video, *extras],
                "write_meta": True,
                "series": series,
                "number": number,
                "title": f"{SERIES_PREFIX.get(series, series)} {number:02d}",
            }


def _write_episode_metadata(folder: Path, package: dict) -> None:
    meta_path = folder / "metadata.json"
    if meta_path.is_file():
        return
    series = package.get("series") or ""
    number = int(package.get("number") or 1)
    video_name = ""
    for item in package.get("files") or []:
        name = item.get("name") or ""
        if Path(name).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
            video_name = name
            break
    payload = {
        "id": package["name"],
        "title": package.get("title") or package["name"],
        "description": f"A Puzmania short from the {SERIES_PREFIX.get(series, series)} series. Episode {number:02d}.",
        "tags": ["puzmania", "shorts", series.lower(), "puzzle"],
        "filename": video_name,
    }
    meta_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _list_children(service, folder_id: str) -> list[dict]:
    files: list[dict] = []
    token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                pageSize=1000,
                pageToken=token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files.extend(response.get("files") or [])
        token = response.get("nextPageToken")
        if not token:
            break
    return files


def _wanted_file(item: dict) -> bool:
    name = item.get("name") or ""
    if not FILE_NAME.match(name):
        return False
    if (item.get("mimeType") or "").startswith("application/vnd.google-apps"):
        return False
    return Path(name).suffix.lower() in KEEP_SUFFIXES


def _up_to_date(local: Path, remote: dict) -> bool:
    if not local.exists():
        return False
    remote_size = int(remote.get("size") or 0)
    if remote_size:
        return local.stat().st_size == remote_size
    return False


def _download_file(service, file_id: str, dest: Path) -> None:
    from googleapiclient.http import MediaIoBaseDownload

    tmp = dest.with_name(dest.name + ".part")
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with tmp.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    tmp.replace(dest)
