from sqlalchemy import create_engine

from app.core.config import Settings
from app.db.session import create_db_engine


def test_database_pool_defaults_are_worker_safe():
    settings = Settings(_env_file=None)

    assert settings.sqlalchemy_sync_pool_size == 2
    assert settings.sqlalchemy_sync_max_overflow == 4
    assert settings.sqlalchemy_async_pool_size == 2
    assert settings.sqlalchemy_async_max_overflow == 4
    assert settings.sqlalchemy_sync_pool_timeout == 30
    assert settings.sqlalchemy_async_pool_timeout == 30
    assert settings.sqlalchemy_sync_pool_recycle == 1800
    assert settings.sqlalchemy_async_pool_recycle == 1800


def test_database_pool_env_overrides_are_parsed(monkeypatch):
    monkeypatch.setenv("SQLALCHEMY_SYNC_POOL_SIZE", "7")
    monkeypatch.setenv("SQLALCHEMY_SYNC_MAX_OVERFLOW", "8")
    monkeypatch.setenv("SQLALCHEMY_SYNC_POOL_TIMEOUT", "9")
    monkeypatch.setenv("SQLALCHEMY_SYNC_POOL_RECYCLE", "10")
    monkeypatch.setenv("SQLALCHEMY_ASYNC_POOL_SIZE", "11")
    monkeypatch.setenv("SQLALCHEMY_ASYNC_MAX_OVERFLOW", "12")
    monkeypatch.setenv("SQLALCHEMY_ASYNC_POOL_TIMEOUT", "13")
    monkeypatch.setenv("SQLALCHEMY_ASYNC_POOL_RECYCLE", "14")

    settings = Settings(_env_file=None)

    assert settings.sync_database_pool_kwargs() == {
        "pool_size": 7,
        "max_overflow": 8,
        "pool_timeout": 9,
        "pool_recycle": 10,
    }
    assert settings.async_database_pool_kwargs() == {
        "pool_size": 11,
        "max_overflow": 12,
        "pool_timeout": 13,
        "pool_recycle": 14,
    }


def test_database_pool_kwargs_are_omitted_for_sqlite():
    settings = Settings(_env_file=None)

    assert settings.sync_database_pool_kwargs("sqlite:///:memory:") == {}
    assert settings.async_database_pool_kwargs("sqlite+aiosqlite:///:memory:") == {}


def test_create_db_engine_uses_configured_sync_pool_kwargs():
    settings = Settings(
        _env_file=None,
        sqlalchemy_sync_pool_size=3,
        sqlalchemy_sync_max_overflow=4,
        sqlalchemy_sync_pool_timeout=5,
        sqlalchemy_sync_pool_recycle=6,
    )

    engine = create_db_engine(
        "postgresql+psycopg://worldfork:worldfork@localhost:5432/worldfork",
        settings=settings,
    )

    try:
        assert engine.pool.size() == 3
        assert engine.pool._max_overflow == 4
        assert engine.pool._timeout == 5
        assert engine.pool._recycle == 6
    finally:
        engine.dispose()
