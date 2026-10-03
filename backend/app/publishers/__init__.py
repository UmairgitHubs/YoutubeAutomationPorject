from app.publishers.instagram import InstagramPublisher
from app.publishers.tiktok import TikTokPublisher
from app.publishers.youtube import YouTubePublisher

PUBLISHERS = {
    "youtube": YouTubePublisher(),
    "tiktok": TikTokPublisher(),
    "instagram": InstagramPublisher(),
}
