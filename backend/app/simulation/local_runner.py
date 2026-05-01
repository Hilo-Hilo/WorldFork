"""Local single-process tick runner.

This module wraps :func:`tick_runner.run_tick` for callers that don't have
a Celery broker available — integration tests, the FastAPI fallback path
for ``POST /api/universes/{id}/step``, and CLI dev tooling.

Everything runs inline on the caller's event loop using the local
``asyncio.gather`` deliberation dispatcher.  No Redis SETNX guard is
required because the caller owns concurrency in this mode (typically a
single-process invocation).

Public API:
    :func:`run_tick_locally`
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.providers.rate_limits import ProviderRateLimiter
from backend.app.providers.routing import RoutingTable
from backend.app.domains.tick.tick_runner import TickContext, run_tick
from backend.app.storage.ledger import Ledger

if TYPE_CHECKING:
    from backend.app.memory.base import MemoryProvider

_log = logging.getLogger(__name__)


async def run_tick_locally(
    ctx: TickContext,
    *,
    session: AsyncSession,
    ledger: Ledger,
    routing: RoutingTable,
    limiter: ProviderRateLimiter,
    memory: MemoryProvider | None = None,
) -> dict:
    """Run a tick end-to-end inline without Celery.

    This is the harness used by the integration tests and by the FastAPI
    step endpoint when the broker is unreachable.  It is a thin pass-through
    to :func:`backend.app.domains.tick.tick_runner.run_tick` with the local
    asyncio.gather deliberation dispatcher.
    """
    _log.info(
        "run_tick_locally: run=%s universe=%s tick=%d (attempt=%d)",
        ctx.run_id, ctx.universe_id, ctx.tick, ctx.attempt_number,
    )
    return await run_tick(
        ctx,
        session=session,
        ledger=ledger,
        routing=routing,
        limiter=limiter,
        memory=memory,
        dispatcher=None,  # → defaults to local asyncio.gather
    )


__all__ = ["run_tick_locally"]
