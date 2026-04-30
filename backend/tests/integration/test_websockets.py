"""Integration tests for the WebSocket live-update endpoints.

Tests use FastAPI's synchronous ``TestClient`` (Starlette's websocket_connect)
and fakeredis as the Redis back-end so no real Redis is required.

The heartbeat interval is reduced to 1 second via the
``WF_WS_HEARTBEAT_SECS`` environment variable so that heartbeat tests
complete quickly.

``receive_json()`` in Starlette's TestClient is synchronous and blocking;
it does not accept a timeout argument.  Tests that need to receive after a
delay use a background thread to publish before or immediately after the
``websocket_connect`` context is entered.

Timing concern: the background publisher threads sleep 0.1–0.15 s before
publishing.  On very slow CI machines this could race against the subscribe.
The design is deliberately simple — if a test is flaky, increasing the
``delay`` constant is the fix.
"""
from __future__ import annotations

import asyncio
import threading
from unittest import mock

import orjson
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Guard: skip the whole module if fakeredis isn't available
# ---------------------------------------------------------------------------
try:
    import fakeredis.aioredis as _fake_aioredis  # noqa: F401

    _FAKEREDIS_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001
    _FAKEREDIS_AVAILABLE = False
    _FAKEREDIS_SKIP_REASON = f"fakeredis.aioredis not available: {_exc}"

if not _FAKEREDIS_AVAILABLE:
    pytest.skip(_FAKEREDIS_SKIP_REASON, allow_module_level=True)

import fakeredis.aioredis as fake_aioredis  # noqa: E402


# ---------------------------------------------------------------------------
# Shared FakeRedis server — lets subscriber and publisher share state
# ---------------------------------------------------------------------------

# A single FakeServer makes all FakeRedis instances see the same data.
_FAKE_SERVER = fake_aioredis.FakeServer()


def _new_fake_redis() -> fake_aioredis.FakeRedis:
    """Return a FakeRedis connected to the shared fake server."""
    return fake_aioredis.FakeRedis(server=_FAKE_SERVER)


# ---------------------------------------------------------------------------
# Background publish helper
# ---------------------------------------------------------------------------


def _publish_after(
    channel: str,
    event_type: str,
    payload: dict,
    delay: float = 0.15,
) -> threading.Thread:
    """Publish an event on *channel* after *delay* seconds, in a daemon thread.

    The publisher uses its own event loop and a new FakeRedis connected to
    the shared _FAKE_SERVER so it sees the same state as the subscriber.
    """

    async def _run() -> None:
        await asyncio.sleep(delay)
        r = _new_fake_redis()
        envelope = {
            "type": event_type,
            "ts": "2026-01-01T00:00:00.000000Z",
            "payload": payload,
        }
        await r.publish(channel, orjson.dumps(envelope))
        await r.aclose(close_connection_pool=True)

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speed up heartbeat to 1 second for all tests in this module."""
    monkeypatch.setenv("WF_WS_HEARTBEAT_SECS", "1")
    for name in (
        "WORLDFORK_SESSION_TOKEN",
        "WORLDFORK_API_TOKEN",
        "WF_SESSION_TOKEN",
        "WF_API_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    import backend.app.api.websockets as _ws

    monkeypatch.setattr(_ws, "_HEARTBEAT_SECS", 1.0)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    """FastAPI TestClient with the Redis layer replaced by fakeredis."""
    monkeypatch.setenv("AUTO_CREATE_TABLES", "false")
    fake_redis_instance = _new_fake_redis()

    # Patch get_redis_client at the module level used by websockets.py and pubsub.py.
    # We also need to clear / bypass the lru_cache so the fake instance is used.
    with (
        mock.patch(
            "backend.app.core.redis_client.get_redis_client",
            return_value=fake_redis_instance,
        ),
        mock.patch(
            "backend.app.api.websockets.get_redis_client",
            return_value=fake_redis_instance,
        ),
        mock.patch(
            "backend.app.api.pubsub.get_redis_client",
            return_value=fake_redis_instance,
        ),
    ):
        from backend.app.main import create_app
        import backend.app.main as main_module

        main_module.settings_obj.auto_create_tables = False

        _app = create_app()
        with TestClient(_app, raise_server_exceptions=False) as tc:
            yield tc
        asyncio.run(fake_redis_instance.aclose(close_connection_pool=True))


# ---------------------------------------------------------------------------
# Tests: /ws/runs/{run_id}
# ---------------------------------------------------------------------------


class TestRunWebSocket:
    def test_connect_and_receive_message(self, client: "TestClient") -> None:
        """Publish to run channel; subscriber receives the message."""
        run_id = "run-abc-123"
        channel = f"run:{run_id}"
        _publish_after(channel, "tick.completed", {"tick": 1})

        with client.websocket_connect(f"/ws/runs/{run_id}?token=valid-token") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "tick.completed"
            assert msg["payload"]["tick"] == 1

    def test_no_token_closes_with_policy_violation(self, client: "TestClient") -> None:
        """Connecting without a token or cookie must be rejected (WS_1008)."""
        # The server closes the socket before accept(); Starlette raises on receive.
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/runs/some-run") as ws:
                ws.receive_json()

    def test_heartbeat_ping_arrives(self, client: "TestClient") -> None:
        """After ~1 s the server sends {type: 'ping'} if no other messages arrive."""
        run_id = "run-heartbeat-test"

        with client.websocket_connect(f"/ws/runs/{run_id}?token=ok") as ws:
            # Heartbeat is 1 s; receive_json blocks until a message arrives.
            ping = ws.receive_json()
            assert ping.get("type") == "ping"


# ---------------------------------------------------------------------------
# Tests: /ws/universes/{universe_id}
# ---------------------------------------------------------------------------


class TestUniverseWebSocket:
    def test_connect_and_receive_message(self, client: "TestClient") -> None:
        uid = "u-001"
        channel = f"universe:{uid}"
        _publish_after(channel, "cohort.split", {"parent": "c1", "children": ["c2", "c3"]})

        with client.websocket_connect(f"/ws/universes/{uid}?token=ok") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "cohort.split"
            assert msg["payload"]["parent"] == "c1"

    def test_no_token_closes_with_policy_violation(self, client: "TestClient") -> None:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/universes/u-001") as ws:
                ws.receive_json()

    def test_god_decision_event(self, client: "TestClient") -> None:
        uid = "u-god"
        channel = f"universe:{uid}"
        _publish_after(channel, "god.decision", {"decision": "spawn_active"})

        with client.websocket_connect(f"/ws/universes/{uid}?token=ok") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "god.decision"
            assert msg["payload"]["decision"] == "spawn_active"


# ---------------------------------------------------------------------------
# Tests: /ws/jobs
# ---------------------------------------------------------------------------


class TestJobsWebSocket:
    def test_connect_and_receive_message(self, client: "TestClient") -> None:
        channel = "jobs:global"
        _publish_after(channel, "job.enqueued", {"job_id": "j-1", "queue": "p0"})

        with client.websocket_connect("/ws/jobs?token=test-token") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "job.enqueued"
            assert msg["payload"]["job_id"] == "j-1"

    def test_no_token_closes_with_policy_violation(self, client: "TestClient") -> None:
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/jobs") as ws:
                ws.receive_json()

    def test_invalid_non_empty_token_rejected_when_token_configured(
        self,
        client: "TestClient",
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("WORLDFORK_API_TOKEN", "configured-token")

        with pytest.raises(Exception):
            with client.websocket_connect("/ws/jobs?token=not-the-token") as ws:
                ws.receive_json()

        with client.websocket_connect("/ws/jobs?token=configured-token") as ws:
            ping = ws.receive_json()
            assert ping.get("type") == "ping"

    def test_heartbeat_ping_arrives(self, client: "TestClient") -> None:
        with client.websocket_connect("/ws/jobs?token=ok") as ws:
            ping = ws.receive_json()
            assert ping.get("type") == "ping"


# ---------------------------------------------------------------------------
# Tests: cookie-based auth
# ---------------------------------------------------------------------------


class TestCookieAuth:
    def test_wf_session_cookie_accepted(self, client: "TestClient") -> None:
        """wf_session cookie authenticates the connection without ?token=."""
        uid = "u-cookie"
        channel = f"universe:{uid}"
        _publish_after(channel, "tick.started", {"tick": 5})
        client.cookies.set("wf_session", "my-session-token")

        try:
            with client.websocket_connect(f"/ws/universes/{uid}") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "tick.started"
                assert msg["payload"]["tick"] == 5
        finally:
            client.cookies.clear()
