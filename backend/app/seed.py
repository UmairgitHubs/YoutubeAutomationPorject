from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import Episode, PublishEvent

SEED_EPISODES = [
    {
        "episode_id": "EP001",
        "title": "First Light Over the Harbour",
        "description": "A quiet open: docks at dawn, radio chatter, and the first map of the season.",
        "tags": ["documentary", "harbour", "dawn"],
        "filename": "EP001_first-light.mp4",
        "duration": "12:04",
        "scheduled": date(2026, 8, 3),
        "youtube": ("success", "dQwY8nHarbour1"),
        "tiktok": ("success", "tt_748201"),
        "instagram": ("success", "ig_192837"),
        "published_at": datetime(2026, 8, 3, 9, 4, 12),
    },
    {
        "episode_id": "EP002",
        "title": "The Last Independent Bookshop",
        "description": "Shelves, dust, and a bookseller who still wraps every purchase by hand.",
        "tags": ["city", "books", "portrait"],
        "filename": "EP002_bookshop.mp4",
        "duration": "11:41",
        "scheduled": date(2026, 8, 4),
        "youtube": ("success", "yt_bookshop02"),
        "tiktok": ("success", "tt_748202"),
        "instagram": ("success", "ig_192838"),
        "published_at": datetime(2026, 8, 4, 9, 3, 44),
    },
    {
        "episode_id": "EP003",
        "title": "Maps Without Borders",
        "description": "Cartographers, faded atlases, and the argument over what a coastline is.",
        "tags": ["maps", "history", "essay"],
        "filename": "EP003_maps.mp4",
        "duration": "13:22",
        "scheduled": date(2026, 8, 5),
        "youtube": ("success", "yt_maps03"),
        "tiktok": ("success", "tt_748203"),
        "instagram": ("success", "ig_192839"),
        "published_at": datetime(2026, 8, 5, 9, 5, 1),
    },
    {
        "episode_id": "EP004",
        "title": "Night Shift at the Signal Tower",
        "description": "Headphones, tea, and a wall of frequencies that never quite go quiet.",
        "tags": ["night", "radio", "work"],
        "filename": "EP004_signal-tower.mp4",
        "duration": "10:58",
        "scheduled": date(2026, 8, 6),
        "youtube": ("success", "yt_signal04"),
        "tiktok": ("success", "tt_748204"),
        "instagram": ("success", "ig_192840"),
        "published_at": datetime(2026, 8, 6, 9, 2, 19),
    },
    {
        "episode_id": "EP005",
        "title": "A Kitchen the Size of a Postcard",
        "description": "One burner, three recipes, and the discipline of cooking for one.",
        "tags": ["food", "city", "routine"],
        "filename": "EP005_kitchen.mp4",
        "duration": "09:47",
        "scheduled": date(2026, 8, 7),
        "youtube": ("success", "yt_kitchen05"),
        "tiktok": ("success", "tt_748205"),
        "instagram": ("success", "ig_192841"),
        "published_at": datetime(2026, 8, 7, 9, 6, 33),
    },
    {
        "episode_id": "EP006",
        "title": "Rain on the Northbound Line",
        "description": "Carriage windows, delayed announcements, and a letter that never gets sent.",
        "tags": ["train", "weather", "travel"],
        "filename": "EP006_northbound.mp4",
        "duration": "12:31",
        "scheduled": date(2026, 8, 10),
        "youtube": ("success", "yt_train06"),
        "tiktok": ("success", "tt_748206"),
        "instagram": ("success", "ig_192842"),
        "published_at": datetime(2026, 8, 10, 9, 3, 8),
    },
    {
        "episode_id": "EP007",
        "title": "The Conservatory After Closing",
        "description": "Glasshouse humidity, moths against the lamps, a pianist warming up alone.",
        "tags": ["music", "night", "architecture"],
        "filename": "EP007_conservatory.mp4",
        "duration": "14:02",
        "scheduled": date(2026, 8, 11),
        "youtube": ("success", "yt_cons07"),
        "tiktok": ("success", "tt_748207"),
        "instagram": ("success", "ig_192843"),
        "published_at": datetime(2026, 8, 11, 9, 4, 51),
    },
    {
        "episode_id": "EP008",
        "title": "Archive Dust",
        "description": "A municipal cellar of tapes, a failing index, and one reel that should not exist.",
        "tags": ["archive", "mystery", "city"],
        "filename": "EP008_archive-dust.mp4",
        "duration": "13:18",
        "scheduled": date(2026, 8, 14),
        "youtube": ("success", "yt_archive08"),
        "tiktok": ("failed", None),
        "instagram": ("success", "ig_192844"),
        "published_at": None,
        "retry_count": 3,
        "last_error": "TikTok: unaudited app cannot publish publicly.",
    },
    {
        "episode_id": "EP009",
        "title": "Letters from a Closed Station",
        "description": "Unclaimed mail, a boarded ticket hall, and names that no longer match the platform.",
        "tags": ["letters", "station", "essay"],
        "filename": "EP009_closed-station.mp4",
        "duration": "11:26",
        "scheduled": date(2026, 8, 15),
        "youtube": ("pending", None),
        "tiktok": ("pending", None),
        "instagram": ("pending", None),
    },
    {
        "episode_id": "EP010",
        "title": "The Bridge That Was Never Named",
        "description": "Concrete, river fog, and the people who cross it twice a day without looking up.",
        "tags": ["bridge", "city", "portrait"],
        "filename": "EP010_unnamed-bridge.mp4",
        "duration": "10:12",
        "scheduled": date(2026, 8, 16),
        "youtube": ("pending", None),
        "tiktok": ("pending", None),
        "instagram": ("pending", None),
    },
    {
        "episode_id": "EP011",
        "title": "Winter Fruit in August",
        "description": "A grocer who imports seasons, and the ethics of a perfect pear.",
        "tags": ["food", "trade", "city"],
        "filename": "EP011_winter-fruit.mp4",
        "duration": "09:55",
        "scheduled": date(2026, 8, 17),
        "youtube": ("pending", None),
        "tiktok": ("pending", None),
        "instagram": ("pending", None),
    },
    {
        "episode_id": "EP012",
        "title": "A Room Above the Printers",
        "description": "Ink in the stairwell, overnight proofs, and the last linotype in the district.",
        "tags": ["print", "work", "night"],
        "filename": "EP012_printers.mp4",
        "duration": "12:47",
        "scheduled": date(2026, 8, 18),
        "youtube": ("pending", None),
        "tiktok": ("pending", None),
        "instagram": ("pending", None),
    },
]

SEED_HISTORY = [
    (datetime(2026, 8, 14, 9, 6, 41), "EP008", "tiktok", "failed", "Unaudited app — public Direct Post denied. Marked for review after 3 retries."),
    (datetime(2026, 8, 14, 9, 5, 12), "EP008", "instagram", "success", "Reel published · ig_192844"),
    (datetime(2026, 8, 14, 9, 4, 2), "EP008", "youtube", "success", "Public · yt_archive08 · thumbnail set"),
    (datetime(2026, 8, 14, 9, 0, 0), "EP008", "system", "info", "Daily run started. Sample history from first install."),
    (datetime(2026, 8, 11, 9, 4, 51), "EP007", "system", "success", "All three platforms succeeded. Summary mailed."),
    (datetime(2026, 8, 10, 9, 3, 8), "EP006", "system", "success", "All three platforms succeeded. Summary mailed."),
    (datetime(2026, 8, 7, 9, 6, 33), "EP005", "system", "success", "All three platforms succeeded. Summary mailed."),
]


def seed_if_empty(db: Session) -> None:
    if db.query(Episode).count() > 0:
        return

    import json

    for index, row in enumerate(SEED_EPISODES, start=1):
        yt, tt, ig = row["youtube"], row["tiktok"], row["instagram"]
        db.add(
            Episode(
                episode_id=row["episode_id"],
                filename=row["filename"],
                title=row["title"],
                description=row["description"],
                tags_hashtags=json.dumps(row["tags"]),
                duration=row["duration"],
                scheduled_date=row["scheduled"],
                queue_order=index,
                youtube_status=yt[0],
                youtube_video_id=yt[1],
                tiktok_status=tt[0],
                tiktok_post_id=tt[1],
                instagram_status=ig[0],
                instagram_media_id=ig[1],
                published_at=row.get("published_at"),
                retry_count=row.get("retry_count", 0),
                last_error=row.get("last_error"),
            )
        )

    for created_at, episode_id, platform, result, detail in SEED_HISTORY:
        db.add(
            PublishEvent(
                created_at=created_at,
                episode_id=episode_id,
                platform=platform,
                result=result,
                detail=detail,
            )
        )
    db.flush()
