from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

settings = get_settings()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(database_url: str, *, settings: Settings | None = None) -> Engine:
    active_settings = settings or get_settings()
    db_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        **active_settings.sync_database_pool_kwargs(database_url),
    )
    if db_engine.url.get_backend_name() == "sqlite":
        event.listen(db_engine, "connect", _enable_sqlite_foreign_keys)
    return db_engine


engine = create_db_engine(settings.database_url_sync, settings=settings)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
