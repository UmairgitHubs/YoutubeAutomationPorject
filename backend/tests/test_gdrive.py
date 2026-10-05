from pathlib import Path
import json

from app.config import Settings
from app.services import gdrive


def test_gdrive_skipped_when_unconfigured(tmp_path):
    cfg = Settings(episodes_dir=tmp_path, gdrive_folder_id="", gdrive_service_account_file="")
    result = gdrive.sync_episodes(cfg)
    assert result["skipped"] is True
    assert result["ok"] is True


def test_package_names_reject_path_traversal():
    assert gdrive.PACKAGE_NAME.match("ALIEN_06")
    assert gdrive.PACKAGE_NAME.match("BLUR_01")
    assert not gdrive.PACKAGE_NAME.match("../secret")
    assert not gdrive.PACKAGE_NAME.match("a/b")
    assert not gdrive.PACKAGE_NAME.match("")


def test_gdrive_downloads_new_package(tmp_path, monkeypatch):
    sa = tmp_path / "sa.json"
    sa.write_text("{}", encoding="utf-8")
    dest = tmp_path / "episodes"
    dest.mkdir()
    cfg = Settings(
        episodes_dir=dest,
        gdrive_folder_id="folder123",
        gdrive_service_account_file=str(sa),
    )
    assert cfg.gdrive_configured()

    monkeypatch.setattr(gdrive, "_drive_service", lambda _settings: object())
    monkeypatch.setattr(
        gdrive,
        "_iter_packages",
        lambda _service, _folder: [
            {
                "name": "ALIEN_99",
                "files": [
                    {"id": "vid", "name": "clip.mp4", "size": "4"},
                    {"id": "meta", "name": "metadata.json", "size": "2"},
                ],
            }
        ],
    )

    def fake_download(_service, file_id, path: Path) -> None:
        path.write_bytes(b"data" if file_id == "vid" else b"{}")

    monkeypatch.setattr(gdrive, "_download_file", fake_download)
    result = gdrive.sync_episodes(cfg)
    assert result["ok"] is True
    assert (dest / "ALIEN_99" / "clip.mp4").read_bytes() == b"data"
    assert "ALIEN_99/clip.mp4" in result["downloaded"]

    result2 = gdrive.sync_episodes(cfg)
    assert result2["downloaded"] == []
    assert result2["updated"] == []


def test_gdrive_status_unconfigured():
    cfg = Settings(gdrive_folder_id="", gdrive_service_account_file="")
    info = gdrive.status(cfg)
    assert info["configured"] is False
    assert info["folderHint"] is None


def test_parse_service_account_json():
    blob = '{"type":"service_account","client_email":"bot@x.iam.gserviceaccount.com","private_key":"x"}'
    info = gdrive.parse_service_account_json(blob)
    assert info["client_email"] == "bot@x.iam.gserviceaccount.com"
    quoted = json.dumps(blob)
    assert gdrive.parse_service_account_json(quoted)["client_email"] == "bot@x.iam.gserviceaccount.com"
    assert gdrive.parse_service_account_json("") is None


def test_iter_packages_from_puz_shorts_folders(monkeypatch):
    children = {
        "root": [
            {"id": "a", "name": "alien_finals", "mimeType": gdrive.FOLDER_MIME},
            {"id": "b", "name": "blur_finals", "mimeType": gdrive.FOLDER_MIME},
            {"id": "j", "name": "jig_finals", "mimeType": gdrive.FOLDER_MIME},
        ],
        "a": [{"id": "av", "name": "alien_01.mp4", "mimeType": "video/mp4", "size": "4"}],
        "b": [{"id": "bv", "name": "blur_02.mp4", "mimeType": "video/mp4", "size": "4"}],
        "j": [{"id": "jv", "name": "jig_01.mp4", "mimeType": "video/mp4", "size": "4"}],
    }

    def fake_list(_service, folder_id):
        return children.get(folder_id, [])

    monkeypatch.setattr(gdrive, "_list_children", fake_list)
    packages = list(gdrive._iter_packages(object(), "root"))
    assert [row["name"] for row in packages] == ["ALIEN_01", "BLUR_02", "JIG_01"]
    assert all(row.get("write_meta") for row in packages)
