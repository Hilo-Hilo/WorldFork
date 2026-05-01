"""WebSocket endpoints for live simulation updates.

Endpoints:
  WS /ws/runs/{run_id}        — channel ``run:{run_id}``
  WS /ws/universes/{universe_id} — channel ``universe:{universe_id}``
  WS /ws/jobs                 — channel ``jobs:global``

Auth: ``wf_session`` cookie OR ``?token=`` query param.

Multi-process safe: each connected client subscribes to the Redis pub/sub
channel directly.  No in-process connection manager is used, so multiple
uvicorn workers function correctly.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Annotated

import orjson
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from fastapi.websockets import WebSocketState

from backend.app.core.redis_client import get_redis_client
from backend.app.core.security import cookie_or_token_from_websocket, verify_token

# Heartbeat interval in seconds.  Tests may override via WF_WS_HEARTBEAT_SECS.
_HEARTBEAT_SECS: float = float(os.environ.get("WF_WS_HEARTBEAT_SECS", "25"))

router = APIRouter(prefix="/ws", tags=["ws"])


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _authorize(
    websocket: WebSocket,
    *,
    cookie_or_token: str | None,
) -> bool:
    """Return True if the token is valid; close the WebSocket and return False otherwise.

    Closes with WS_1008_POLICY_VIOLATION on auth failure.
    Never logs the token value.
    """
    if cookie_or_token and verify_token(cookie_or_token):
        return True
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return False


# ---------------------------------------------------------------------------
# Redis pub/sub async generator
# ---------------------------------------------------------------------------


async def _redis_pubsub_iter(channel: str) -> AsyncIterator[dict]:
    """Subscribe to *channel* and yield decoded JSON messages.

    Yields each received message as a plain ``dict`` (already parsed from JSON).
    Stops cleanly when the generator is closed by the caller (e.g. on disconnect).
    """
    redis = get_redis_client()
    # Create a fresh pub/sub connection for this subscriber.
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for raw_message in pubsub.listen():
            if raw_message is None:
                continue
            if raw_message.get("type") != "message":
                # Skip subscribe/unsubscribe confirmation messages.
                continue
            data = raw_message.get("data")
            if not data:
                continue
            try:
                if isinstance(data, bytes):
                    yield orjson.loads(data)
                else:
                    yield orjson.loads(data.encode())
            except (orjson.JSONDecodeError, ValueError):
                # Ignore malformed messages — never crash the stream.
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Fan-in streaming helper
# ---------------------------------------------------------------------------


async def _stream(websocket: WebSocket, channel: str) -> None:
    """Drive the WebSocket send loop.

    Runs two concurrent tasks:
    * heartbeat ping every ``_HEARTBEAT_SECS`` seconds.
    * forwarding of Redis pub/sub messages to the client.

    Exits cleanly on WebSocketDisconnect or client-side close.
    """

    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECS)
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            try:
                await websocket.send_json({"type": "ping"})
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001
                break

    async def _forward() -> None:
        async for msg in _redis_pubsub_iter(channel):
            if websocket.client_state != WebSocketState.CONNECTED:
                break
            try:
                await websocket.send_json(msg)
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001
                break

    heartbeat_task = asyncio.create_task(_heartbeat())
    forward_task = asyncio.create_task(_forward())

    try:
        # Return when either task finishes (disconnect or error).
        done, pending = await asyncio.wait(
            [heartbeat_task, forward_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in [heartbeat_task, forward_task]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.websocket("/runs/{run_id}")
async def ws_run(
    websocket: WebSocket,
    run_id: str,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Stream live run events for a specific run.

    Streamed event types: tick.completed, tick.started, branch.created,
    branch.frozen, branch.killed, run.status_changed, metrics.updated.

    Auth: ``wf_session`` cookie or ``?token=`` query param.
    """
    resolved_token = token or cookie_or_token_from_websocket(websocket)
    if not await _authorize(websocket, cookie_or_token=resolved_token):
        return
    await websocket.accept()
    channel = f"run:{run_id}"
    try:
        await _stream(websocket, channel)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass


@router.websocket("/universes/{universe_id}")
async def ws_universe(
    websocket: WebSocket,
    universe_id: str,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Stream live universe events for a specific universe.

    Streamed event types: tick.completed, tick.started, branch.created,
    branch.frozen, branch.killed, social_post.created, event.scheduled,
    cohort.split, cohort.merge, god.decision.

    Auth: ``wf_session`` cookie or ``?token=`` query param.
    """
    resolved_token = token or cookie_or_token_from_websocket(websocket)
    if not await _authorize(websocket, cookie_or_token=resolved_token):
        return
    await websocket.accept()
    channel = f"universe:{universe_id}"
    try:
        await _stream(websocket, channel)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass


@router.websocket("/jobs")
async def ws_jobs(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """Stream global job queue updates.

    Streamed event types: queue depth updates, worker status,
    job lifecycle events (enqueued, started, completed, failed, retried).

    Auth: ``wf_session`` cookie or ``?token=`` query param.
    """
    resolved_token = token or cookie_or_token_from_websocket(websocket)
    if not await _authorize(websocket, cookie_or_token=resolved_token):
        return
    await websocket.accept()
    channel = "jobs:global"
    try:
        await _stream(websocket, channel)
    except WebSocketDisconnect:
        pass
    finally:
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass
