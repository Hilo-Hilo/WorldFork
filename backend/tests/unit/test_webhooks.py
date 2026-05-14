"""Unit tests for backend.app.integrations.webhooks."""
from __future__ import annotations

import time

import httpx
import pytest
import respx

from backend.app.integrations.webhooks import (
    WebhookDeliverer,
    WebhookDeliveryError,
    WebhookSigner,
)

SECRET = "test-secret-key-abc123"
PAYLOAD = b'{"event": "run.completed", "run_id": "BB_001"}'


# ---------------------------------------------------------------------------
# WebhookSigner tests
# ---------------------------------------------------------------------------


class TestWebhookSigner:
    def test_sign_returns_tuple(self) -> None:
        signer = WebhookSigner(SECRET)
        sig, ts = signer.sign(PAYLOAD)
        assert isinstance(sig, str)
        assert isinstance(ts, int)
        assert len(sig) == 64  # SHA-256 hex is 64 chars

    def test_verify_happy_path(self) -> None:
        signer = WebhookSigner(SECRET)
        sig, ts = signer.sign(PAYLOAD)
        assert signer.verify(PAYLOAD, sig, ts) is True

    def test_verify_tampered_payload_rejected(self) -> None:
        signer = WebhookSigner(SECRET)
        sig, ts = signer.sign(PAYLOAD)
        tampered = PAYLOAD + b" extra"
        assert signer.verify(tampered, sig, ts) is False

    def test_verify_stale_timestamp_rejected(self) -> None:
        signer = WebhookSigner(SECRET)
        old_ts = int(time.time()) - 400  # older than default 300 s
        sig, _ = signer.sign(PAYLOAD, timestamp=old_ts)
        assert signer.verify(PAYLOAD, sig, old_ts, max_age_seconds=300) is False

    def test_verify_within_max_age_accepted(self) -> None:
        signer = WebhookSigner(SECRET)
        recent_ts = int(time.time()) - 100
        sig, _ = signer.sign(PAYLOAD, timestamp=recent_ts)
        assert signer.verify(PAYLOAD, sig, recent_ts, max_age_seconds=300) is True

    def test_verify_wrong_secret_rejected(self) -> None:
        signer1 = WebhookSigner(SECRET)
        signer2 = WebhookSigner("other-secret")
        sig, ts = signer1.sign(PAYLOAD)
        assert signer2.verify(PAYLOAD, sig, ts) is False

    def test_sign_with_explicit_timestamp(self) -> None:
        signer = WebhookSigner(SECRET)
        ts_fixed = 1700000000
        sig, ts_out = signer.sign(PAYLOAD, timestamp=ts_fixed)
        assert ts_out == ts_fixed
        # Verify with a large max_age to avoid time-travel rejection
        assert signer.verify(PAYLOAD, sig, ts_fixed, max_age_seconds=10**9) is True

    @pytest.mark.parametrize(
        ("signature_value", "timestamp_value", "expected"),
        [
            ("valid", "1700000000", True),
            ("valid", None, False),
            ("valid", "", False),
            ("valid", "not-a-timestamp", False),
            ("valid", [], False),
            ("valid", {}, False),
            (None, 1700000000, False),
            (b"bytes-signature", 1700000000, False),
            (12345, 1700000000, False),
            (["signature"], 1700000000, False),
        ],
    )
    def test_verify_handles_header_derived_values_without_raising(
        self,
        monkeypatch,
        signature_value,
        timestamp_value,
        expected,
    ) -> None:
        signer = WebhookSigner(SECRET)
        valid_signature, _ = signer.sign(PAYLOAD, timestamp=1700000000)
        signature = valid_signature if signature_value == "valid" else signature_value
        monkeypatch.setattr(time, "time", lambda: 1700000000)

        assert signer.verify(PAYLOAD, signature, timestamp_value) is expected


# ---------------------------------------------------------------------------
# WebhookDeliverer tests
# ---------------------------------------------------------------------------

TARGET_URL = "https://example.com/webhooks"
EVENT = {"type": "run.completed", "run_id": "BB_001"}


class TestWebhookDeliverer:
    @pytest.mark.asyncio
    @respx.mock
    async def test_deliver_success(self) -> None:
        """Successful 2xx delivery returns a result dict."""
        respx.post(TARGET_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

        signer = WebhookSigner(SECRET)
        async with httpx.AsyncClient() as client:
            deliverer = WebhookDeliverer(signer, http_client=client, max_attempts=3)
            result = await deliverer.deliver(url=TARGET_URL, event=EVENT)

        assert result["status_code"] == 200
        assert result["attempts"] == 1
        assert "latency_ms" in result
        assert "delivered_at" in result

    @pytest.mark.asyncio
    @respx.mock
    async def test_deliver_retries_on_5xx(self) -> None:
        """Deliverer retries on 5xx and succeeds on second attempt."""
        respx.post(TARGET_URL).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        signer = WebhookSigner(SECRET)
        async with httpx.AsyncClient() as client:
            deliverer = WebhookDeliverer(signer, http_client=client, max_attempts=5)
            result = await deliverer.deliver(url=TARGET_URL, event=EVENT)

        assert result["status_code"] == 200
        assert result["attempts"] == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_deliver_raises_after_max_attempts(self) -> None:
        """WebhookDeliveryError raised after all attempts return 5xx."""
        respx.post(TARGET_URL).mock(return_value=httpx.Response(500))

        signer = WebhookSigner(SECRET)
        async with httpx.AsyncClient() as client:
            deliverer = WebhookDeliverer(signer, http_client=client, max_attempts=3)
            with pytest.raises(WebhookDeliveryError):
                await deliverer.deliver(url=TARGET_URL, event=EVENT)

    @pytest.mark.asyncio
    @respx.mock
    async def test_deliver_4xx_not_retried(self) -> None:
        """A 4xx response is not retried (not a transient server error)."""
        respx.post(TARGET_URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))

        signer = WebhookSigner(SECRET)
        async with httpx.AsyncClient() as client:
            deliverer = WebhookDeliverer(signer, http_client=client, max_attempts=5)
            result = await deliverer.deliver(url=TARGET_URL, event=EVENT)

        # 400 returned on first attempt, not retried
        assert result["status_code"] == 400
        assert result["attempts"] == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_headers_contain_signature(self) -> None:
        """Delivery request includes WorldFork-Signature and WorldFork-Event headers."""
        captured_request: httpx.Request | None = None

        def capture(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200)

        respx.post(TARGET_URL).mock(side_effect=capture)

        signer = WebhookSigner(SECRET)
        async with httpx.AsyncClient() as client:
            deliverer = WebhookDeliverer(signer, http_client=client)
            await deliverer.deliver(url=TARGET_URL, event=EVENT)

        assert captured_request is not None
        assert "worldfork-signature" in captured_request.headers
        sig_header = captured_request.headers["worldfork-signature"]
        assert sig_header.startswith("t=") and "v1=" in sig_header
        assert captured_request.headers.get("worldfork-event") == "run.completed"
