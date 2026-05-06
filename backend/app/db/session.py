from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(database_url: str) -> Engine:
    # Pool needs to comfortably exceed `max_parallel_cohort_decisions` (16 by
    # default) since the per-tick cohort/hero fan-out opens one Session per
    # worker thread. Default 5+10 was hitting QueuePool timeout mid-run.
    is_sqlite = database_url.startswith("sqlite")
    kwargs: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        kwargs.update(pool_size=20, max_overflow=20, pool_recycle=1800)
    db_engine = create_engine(database_url, **kwargs)
    if db_engine.url.get_backend_name() == "sqlite":
        event.listen(db_engine, "connect", _enable_sqlite_foreign_keys)
    return db_engine


engine = create_db_engine(settings.database_url_sync)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
