"""Pytest fixtures for the WorldFork backend test suite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator

import backend.app.models  # noqa: F401 - populate Base.metadata for SQLite type patching
from backend.app.models.base import Base

_ARRAY_BIND_PROCESSOR = ARRAY.bind_processor
_ARRAY_RESULT_PROCESSOR = ARRAY.result_processor


class SQLiteJSON(TypeDecorator):
    """SQLite-compatible JSON storage for Postgres JSONB and ARRAY columns."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect):  # noqa: ANN001
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect):  # noqa: ANN001
        if value is None or not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


def _patch_sqlite_json_types() -> None:
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, SQLiteJSON):
                continue
            if isinstance(col.type, JSONB | ARRAY):
                col.type = SQLiteJSON()


def _patch_sqlite_array_processors() -> None:
    if getattr(ARRAY, "_worldfork_sqlite_patch", False):
        return

    def bind_processor(self, dialect):  # noqa: ANN001
        if dialect.name != "sqlite":
            return _ARRAY_BIND_PROCESSOR(self, dialect)

        def process(value: Any) -> Any:
            if value is None or isinstance(value, str):
                return value
            return json.dumps(value)

        return process

    def result_processor(self, dialect, coltype):  # noqa: ANN001
        if dialect.name != "sqlite":
            return _ARRAY_RESULT_PROCESSOR(self, dialect, coltype)

        def process(value: Any) -> Any:
            if value is None or isinstance(value, list):
                return value
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return value

        return process

    ARRAY.bind_processor = bind_processor  # type: ignore[method-assign]
    ARRAY.result_processor = result_processor  # type: ignore[method-assign]
    ARRAY._worldfork_sqlite_patch = True  # type: ignore[attr-defined]


_patch_sqlite_array_processors()
_patch_sqlite_json_types()


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default asyncio event loop policy (session-scoped)."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def tmp_run_root(tmp_path: Path) -> Path:
    """Return a temporary directory that mimics the RUN_ROOT layout."""
    run_root = tmp_path / "runs"
    run_root.mkdir()
    return run_root
