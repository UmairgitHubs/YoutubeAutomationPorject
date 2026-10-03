from datetime import date, datetime

from pydantic import BaseModel, Field


class PlatformStatus(BaseModel):
    status: str
    id: str | None = None
    error: str | None = None


class EpisodeOut(BaseModel):
    id: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    filename: str
    duration: str | None = None
    scheduled: date | None = None
    youtube: PlatformStatus
    tiktok: PlatformStatus
    instagram: PlatformStatus
    publishedAt: datetime | None = None
    retryCount: int = 0
    lastError: str | None = None
    overall: str = "queued"
    campaignId: str | None = None


class SettingsIn(BaseModel):
    publishTime: str | None = None
    timezone: str | None = None
    episodesPerRun: int | None = None
    retryAttempts: int | None = None
    retryBackoff: str | None = None
    notifyEmail: bool | None = None
    notifySlack: bool | None = None
    alertOnFailure: bool | None = None
    dryRun: bool | None = None
    winnerMode: str | None = None
    pointsMultiplier: int | None = None
    winnerIntroDays: int | None = None
    platforms: dict[str, dict] | None = None


class SettingsOut(BaseModel):
    publishTime: str
    timezone: str
    episodesPerRun: int
    retryAttempts: int
    retryBackoff: str
    notifyEmail: bool
    notifySlack: bool
    alertOnFailure: bool
    dryRun: bool
    winnerMode: str = "seed"
    pointsMultiplier: int = 2500
    winnerIntroDays: int = 3
    platforms: dict[str, dict]


class HistoryOut(BaseModel):
    at: datetime
    episode: str
    platform: str
    result: str
    detail: str


class ReorderIn(BaseModel):
    episode_id: str
    direction: int


class FeatureWinnerIn(BaseModel):
    memberId: int | None = None
    username: str | None = None
    points: int | None = None
    showDate: date | None = None


class PlatformPatch(BaseModel):
    enabled: bool | None = None


class PublishResultOut(BaseModel):
    episode_id: str
    dry_run: bool
    results: dict[str, PlatformStatus]
    overall: str
    summary: str
