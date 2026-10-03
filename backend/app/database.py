from collections.abc import Generator
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _sqlite_url(path) -> str:
    return f"sqlite:///{path.as_posix()}"


settings = get_settings()
settings.database_path.parent.mkdir(parents=True, exist_ok=True)
settings.log_dir.mkdir(parents=True, exist_ok=True)
settings.episodes_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _sqlite_url(settings.database_path),
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def safe_flush(db: Session) -> None:
    last: OperationalError | None = None
    for attempt in range(8):
        try:
            db.flush()
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            time.sleep(0.25 * (attempt + 1))
    if last:
        raise last


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        _commit(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _commit(db: Session) -> None:
    last: OperationalError | None = None
    for attempt in range(8):
        try:
            db.commit()
            return
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            last = exc
            time.sleep(0.25 * (attempt + 1))
    if last:
        raise last
