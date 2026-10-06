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
    monkeypatch.setattr(gdrive, "_require_folder", lambda *_args, **_kwargs: {"id": "folder123", "name": "episodes"})
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


def test_each_video_is_ready_before_the_next_download(tmp_path, monkeypatch):
    sa = tmp_path / "sa.json"
    sa.write_text("{}", encoding="utf-8")
    dest = tmp_path / "episodes"
    dest.mkdir()
    cfg = Settings(
        episodes_dir=dest,
        gdrive_folder_id="folder123",
        gdrive_service_account_file=str(sa),
    )
    monkeypatch.setattr(gdrive, "_drive_service", lambda _settings: object())
    monkeypatch.setattr(gdrive, "_require_folder", lambda *_args, **_kwargs: {"id": "folder123", "name": "episodes"})
    monkeypatch.setattr(
        gdrive,
        "_iter_packages",
        lambda _service, _folder: [
            {"name": "JIG_02", "files": [{"id": "v2", "name": "jig.mp4", "size": "1"}], "write_meta": True, "series": "JIG", "number": 2, "title": "Jig Puzzle 02"},
            {"name": "ALIEN_01", "files": [{"id": "v1", "name": "alien.mp4", "size": "1"}], "write_meta": True, "series": "ALIEN", "number": 1, "title": "Alien Monkeys 01"},
        ],
    )
    calls: list[str] = []

    def fake_download(_service, file_id, path: Path) -> None:
        calls.append(f"download:{file_id}")
        path.write_bytes(b"v")

    def on_ready(folder: Path) -> None:
        calls.append(f"ready:{folder.name}")
        assert any(path.suffix == ".mp4" and path.stat().st_size for path in folder.iterdir())

    monkeypatch.setattr(gdrive, "_download_file", fake_download)
    result = gdrive.sync_episodes(cfg, on_ready=on_ready)
    assert result["ok"] is True
    assert calls == ["download:v1", "ready:ALIEN_01", "download:v2", "ready:JIG_02"]


def test_gdrive_status_unconfigured():
    cfg = Settings(gdrive_folder_id="", gdrive_service_account_file="")
    info = gdrive.status(cfg)
    assert info["configured"] is False
    assert info["folderHint"] is None
    assert info["shareEmail"] is None


def test_gdrive_status_includes_share_email(tmp_path):
    sa = tmp_path / "sa.json"
    sa.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "bot@x.iam.gserviceaccount.com",
                "private_key": "x",
            }
        ),
        encoding="utf-8",
    )
    cfg = Settings(gdrive_folder_id="abc12345678", gdrive_service_account_file=str(sa))
    info = gdrive.status(cfg)
    assert info["configured"] is True
    assert info["shareEmail"] == "bot@x.iam.gserviceaccount.com"
    assert info["folderHint"] == "12345678"


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


def test_iter_packages_inside_episodes_wrapper(monkeypatch):
    children = {
        "puzmania": [{"id": "eps", "name": "episodes", "mimeType": gdrive.FOLDER_MIME}],
        "eps": [
            {
                "id": "a1",
                "name": "ALIEN_01",
                "mimeType": gdrive.FOLDER_MIME,
            },
            {
                "id": "j1",
                "name": "JIG_01",
                "mimeType": gdrive.FOLDER_MIME,
            },
        ],
        "a1": [{"id": "av", "name": "alien_monkeys_01.mp4", "mimeType": "video/mp4", "size": "4"}],
        "j1": [
            {"id": "jv", "name": "jig_01.mp4", "mimeType": "video/mp4", "size": "4"},
            {"id": "jm", "name": "metadata.json", "mimeType": "application/json", "size": "2"},
        ],
    }

    def fake_list(_service, folder_id):
        return children.get(folder_id, [])

    monkeypatch.setattr(gdrive, "_list_children", fake_list)
    packages = list(gdrive._iter_packages(object(), "puzmania"))
    assert {row["name"] for row in packages} == {"ALIEN_01", "JIG_01"}
    alien = next(row for row in packages if row["name"] == "ALIEN_01")
    assert alien["files"][0]["name"] == "alien_monkeys_01.mp4"
