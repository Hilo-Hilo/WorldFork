"""Async and sync database engine + session factories."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import settings

# ---------------------------------------------------------------------------
# Async engine (used by FastAPI / application code)
# ---------------------------------------------------------------------------
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    **settings.async_database_pool_kwargs(),
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with SessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Sync engine — used by Alembic and scripts that cannot be async
# ---------------------------------------------------------------------------
sync_engine = create_engine(
    settings.database_url_sync,
    pool_pre_ping=True,
    echo=False,
    **settings.sync_database_pool_kwargs(),
)

SyncSessionLocal: sessionmaker[Session] = sessionmaker(
    bind=sync_engine,
    expire_on_commit=False,
)


def get_sync_session() -> Iterator[Session]:
    """Generator dependency that yields a synchronous DB session."""
    session: Session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
