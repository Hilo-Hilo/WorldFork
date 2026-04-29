"""Shared fixtures for B5-A integration tests.

Uses an in-memory SQLite async engine so no external Postgres is required.
JSONB and ARRAY columns are remapped to JSON / TEXT[] for SQLite compatibility.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401 - populate Base.metadata before SQLite type patching
from backend.app.models.base import Base

# ---------------------------------------------------------------------------
# SQLite JSONB / ARRAY shim
# ---------------------------------------------------------------------------
# Patch column types at class definition time so SQLite accepts them.
# This runs once at import time before any engine is created.

_JSONB_ORIGINAL = JSONB
_ARRAY_ORIGINAL = ARRAY


def _patch_sqlite_types() -> None:
    """Replace Postgres-specific column types with SQLite-compatible equivalents.

    We monkey-patch the ORM model classes so that SQLite can create the tables.
    In real Postgres mode the original types are still used.
    """

    # Replace JSONB with JSON in all mapped columns.
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            elif isinstance(col.type, ARRAY):
                # ARRAY(String) → JSON (store as JSON array)
                col.type = JSON()


_patch_sqlite_types()


# ---------------------------------------------------------------------------
# SQLite in-memory async engine
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def test_engine():
    """Function-scoped async SQLite engine with all tables created.

    Several integration routes intentionally commit. A fresh in-memory engine
    per test keeps those commits from leaking into later tests.
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    """Function-scoped async session — rolls back after each test."""
    factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_engine, db_session):
    """AsyncClient backed by ASGI transport with DB override."""
    from backend.app.core.db import get_session
    from backend.app.main import app

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.pop(get_session, None)
