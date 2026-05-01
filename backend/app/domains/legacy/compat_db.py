from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession


async def table_has_columns(
    session: AsyncSession,
    table_name: str,
    columns: Iterable[str],
) -> bool:
    """Return whether the current DB has the requested table/column shape."""

    wanted = set(columns)

    def _check(sync_session) -> bool:  # noqa: ANN001
        inspector = inspect(sync_session.connection())
        try:
            available = {column["name"] for column in inspector.get_columns(table_name)}
        except Exception:
            return False
        return wanted.issubset(available)

    try:
        return bool(await session.run_sync(_check))
    except SQLAlchemyError:
        await session.rollback()
        return False
