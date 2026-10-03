from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("puzmania.migrate")

EPISODE_COLUMNS = {
    "campaign_id": "VARCHAR(32)",
}
MEMBER_COLUMNS = {
    "visitor_token": "VARCHAR(64)",
    "channel_points": "INTEGER DEFAULT 0",
}
PUZZLE_COLUMNS = {
    "episode_id": "VARCHAR(48)",
    "series": "VARCHAR(16)",
}


def migrate_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "episodes" in tables:
            existing = {col["name"] for col in inspector.get_columns("episodes")}
            for name, ddl in EPISODE_COLUMNS.items():
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE episodes ADD COLUMN {name} {ddl}"))
                log.info("Added episodes.%s", name)
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_episodes_campaign_id ON episodes (campaign_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_episodes_scheduled_date ON episodes (scheduled_date)"))
        if "members" in tables:
            existing = {col["name"] for col in inspector.get_columns("members")}
            for name, ddl in MEMBER_COLUMNS.items():
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE members ADD COLUMN {name} {ddl}"))
                log.info("Added members.%s", name)
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_members_visitor_token ON members (visitor_token)"))
        if "puzzles" in tables:
            existing = {col["name"] for col in inspector.get_columns("puzzles")}
            for name, ddl in PUZZLE_COLUMNS.items():
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE puzzles ADD COLUMN {name} {ddl}"))
                log.info("Added puzzles.%s", name)
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_puzzles_episode_id ON puzzles (episode_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_puzzles_series ON puzzles (series)"))
        if "puzzle_attempts" not in tables:
            conn.execute(
                text(
                    "CREATE TABLE puzzle_attempts ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "member_id INTEGER NOT NULL, "
                    "puzzle_id INTEGER NOT NULL, "
                    "started_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "finished_at DATETIME, "
                    "choice VARCHAR(128) DEFAULT '', "
                    "correct BOOLEAN DEFAULT 0, "
                    "timer_left INTEGER DEFAULT 0, "
                    "points_awarded INTEGER DEFAULT 0, "
                    "message VARCHAR(160) DEFAULT '', "
                    "created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
                    "UNIQUE (member_id, puzzle_id)"
                    ")"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_puzzle_attempts_member_id ON puzzle_attempts (member_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_puzzle_attempts_puzzle_id ON puzzle_attempts (puzzle_id)"))
            log.info("Created puzzle_attempts")
