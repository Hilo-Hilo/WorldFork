"""Redis pub/sub publisher helpers used by Celery tasks and API code.

Multi-process safe: all publishing goes through Redis pub/sub so that
multiple uvicorn workers and multiple Celery workers each independently
push events without shared in-process state.
"""
from __future__ import annotations

from datetime import UTC, datetime

import orjson

from backend.app.core.redis_client import get_redis_client

# ---------------------------------------------------------------------------
# Channel name helpers
# ---------------------------------------------------------------------------


def universe_channel(universe_id: str) -> str:
    """Return the Redis pub/sub channel name for a universe."""
    return f"universe:{universe_id}"


def run_channel(run_id: str) -> str:
    """Return the Redis pub/sub channel name for a run."""
    return f"run:{run_id}"


def jobs_channel() -> str:
    """Return the Redis pub/sub channel name for global job updates."""
    return "jobs:global"


# ---------------------------------------------------------------------------
# Core publish helper
# ---------------------------------------------------------------------------


async def publish(channel: str, event_type: str, payload: dict) -> None:
    """Publish an event to a Redis pub/sub channel.

    Builds the envelope:
        {type: event_type, ts: utc_now_iso, payload: payload}
    and serialises via orjson before publishing.

    This function is intentionally thin so Celery tasks can call it via
    ``asyncio.run(publish(...))``.
    """
    redis = get_redis_client()
    envelope = {
        "type": event_type,
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }
    await redis.publish(channel, orjson.dumps(envelope))
