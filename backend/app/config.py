from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"


def _clean_secret(value: str) -> str:
    text = (value or "").strip().strip('"').strip("'").replace("\r", "").replace("\n", "").replace(" ", "")
    return text.lstrip("\ufeff")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8-sig",
        extra="ignore",
        env_ignore_empty=True,
    )

    app_name: str = "Puzmania"
    host: str = "127.0.0.1"
    port: int = 8088
    timezone: str = "Asia/Karachi"
    publish_time: str = "09:00"
    episodes_per_run: int = 1
    retry_attempts: int = 3
    retry_backoff: str = "exponential"
    dry_run: bool = True

    database_path: Path = PROJECT_ROOT / "data" / "puzmania.db"
    episodes_dir: Path = PROJECT_ROOT / "episodes"
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    log_dir: Path = PROJECT_ROOT / "data" / "logs"

    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = ""
    youtube_privacy: str = "public"
    youtube_redirect_uri: str = ""

    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_access_token: str = ""
    tiktok_refresh_token: str = ""
    tiktok_open_id: str = ""
    tiktok_redirect_uri: str = ""

    instagram_access_token: str = ""
    instagram_ig_user_id: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    gdrive_folder_id: str = ""
    gdrive_service_account_file: str = ""
    gdrive_service_account_json: str = ""
    gdrive_delete_missing: bool = False

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    slack_webhook_url: str = ""

    public_site_url: str = ""
    join_min_age: int = 13

    notify_email: bool = True
    notify_slack: bool = False
    alert_on_failure: bool = True

    youtube_enabled: bool = True
    tiktok_enabled: bool = False
    instagram_enabled: bool = True

    winner_mode: str = "seed"
    points_multiplier: int = 2500
    winner_intro_days: int = 3
    winner_points_min: int = 2000
    winner_points_max: int = 5000

    def youtube_connected(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret and self.youtube_refresh_token)

    def tiktok_connected(self) -> bool:
        return bool(self.tiktok_access_token or self.tiktok_refresh_token)

    def instagram_connected(self) -> bool:
        return bool(self.instagram_access_token and self.instagram_ig_user_id)

    def openai_configured(self) -> bool:
        return bool(_clean_secret(self.openai_api_key))

    def gdrive_credentials_path(self) -> Path | None:
        raw = (self.gdrive_service_account_file or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path if path.is_file() else None

    def gdrive_configured(self) -> bool:
        has_creds = bool((self.gdrive_service_account_json or "").strip()) or self.gdrive_credentials_path() is not None
        return bool(self.gdrive_folder_id.strip()) and bool(has_creds)

    def join_url(self) -> str:
        raw = (self.public_site_url or "").strip().rstrip("/")
        if raw:
            return raw if raw.endswith("/join") else f"{raw}/join"
        return f"http://{self.host}:{self.port}/join"


def get_settings() -> Settings:
    return Settings()
