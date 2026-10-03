from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PLATFORMS = ("youtube", "tiktok", "instagram")
STATUSES = ("pending", "success", "failed", "skipped")


class Episode(Base):
    __tablename__ = "episodes"

    episode_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags_hashtags: Mapped[str] = mapped_column(Text, default="[]")
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    folder_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scheduled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    queue_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    youtube_status: Mapped[str] = mapped_column(String(16), default="pending")
    youtube_video_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tiktok_status: Mapped[str] = mapped_column(String(16), default="pending")
    tiktok_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instagram_status: Mapped[str] = mapped_column(String(16), default="pending")
    instagram_media_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("campaigns.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def status_for(self, platform: str) -> str:
        return getattr(self, f"{platform}_status")

    def set_status(self, platform: str, status: str, post_id: str | None = None) -> None:
        setattr(self, f"{platform}_status", status)
        id_field = {
            "youtube": "youtube_video_id",
            "tiktok": "tiktok_post_id",
            "instagram": "instagram_media_id",
        }[platform]
        if post_id is not None:
            setattr(self, id_field, post_id)

    def post_id_for(self, platform: str) -> str | None:
        return {
            "youtube": self.youtube_video_id,
            "tiktok": self.tiktok_post_id,
            "instagram": self.instagram_media_id,
        }[platform]

    def overall_status(self) -> str:
        statuses = [self.youtube_status, self.tiktok_status, self.instagram_status]
        active = [s for s in statuses if s != "skipped"]
        if not active:
            return "skipped"
        if "failed" in active:
            return "attention"
        if all(s == "success" for s in active):
            return "published"
        return "queued"


class PublishEvent(Base):
    __tablename__ = "publish_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    episode_id: Mapped[str] = mapped_column(String(32), index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    result: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text, default="")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    platforms: Mapped[str] = mapped_column(String(128), default='["youtube","instagram"]')
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    videos_per_day: Mapped[int] = mapped_column(Integer, default=1)
    loop: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    slot_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    age: Mapped[int] = mapped_column(Integer)
    password_hash: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visitor_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    channel_points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Puzzle(Base):
    __tablename__ = "puzzles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(String(128))
    choices: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    episode_id: Mapped[str | None] = mapped_column(String(48), nullable=True, unique=True, index=True)
    series: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)


class PuzzleAttempt(Base):
    __tablename__ = "puzzle_attempts"
    __table_args__ = (UniqueConstraint("member_id", "puzzle_id", name="uq_attempt_member_puzzle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(Integer, index=True)
    puzzle_id: Mapped[int] = mapped_column(Integer, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    choice: Mapped[str] = mapped_column(String(128), default="")
    correct: Mapped[bool] = mapped_column(Boolean, default=False)
    timer_left: Mapped[int] = mapped_column(Integer, default=0)
    points_awarded: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PuzzleRun(Base):
    __tablename__ = "puzzle_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(Integer, index=True)
    play_date: Mapped[date] = mapped_column(Date, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[str] = mapped_column(Text, default="[]")


class DailyWinner(Base):
    __tablename__ = "daily_winners"

    play_date: Mapped[date] = mapped_column(Date, primary_key=True)
    member_id: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ClickEvent(Base):
    __tablename__ = "click_events"
    __table_args__ = (UniqueConstraint("visitor_token", "episode_id", name="uq_click_visitor_episode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_token: Mapped[str] = mapped_column(String(64), index=True)
    member_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    episode_id: Mapped[str] = mapped_column(String(48), default="", index=True)
    platform: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class FeaturedWinner(Base):
    __tablename__ = "featured_winners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    show_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(32))
    points: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="seed")
    member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
