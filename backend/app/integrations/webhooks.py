"""Webhook signing and delivery — Stripe/Slack-compatible HMAC-SHA256 pattern.

WebhookSigner:  signs and verifies payloads with HMAC-SHA256.
WebhookDeliverer: POSTs signed payloads with exponential backoff.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from backend.app.core.clock import now_utc

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WebhookDeliveryError(Exception):
    """Raised after all delivery attempts are exhausted."""


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


class WebhookSigner:
    """Signs and verifies webhook payloads using HMAC-SHA256.

    Signature format mirrors the Stripe/Slack pattern:
        HMAC-SHA256(secret, f"{timestamp}.{payload_bytes}")
    """

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def _compute_signature(self, payload: bytes, timestamp: int) -> str:
        """Return the hex HMAC-SHA256 for the given payload and timestamp."""
        signed_content = f"{timestamp}.".encode() + payload
        return hmac.new(self._secret, signed_content, hashlib.sha256).hexdigest()

    def sign(
        self,
        payload: bytes,
        *,
        timestamp: int | None = None,
    ) -> tuple[str, int]:
        """Sign *payload* and return ``(signature_hex, timestamp)``.

        Args:
            payload: Raw bytes to sign (typically JSON-encoded event body).
            timestamp: Unix epoch seconds. Defaults to the current time.

        Returns:
            A tuple of ``(signature_hex, timestamp_int)``.
        """
        if timestamp is None:
            timestamp = int(time.time())
        sig = self._compute_signature(payload, timestamp)
        return sig, timestamp

    def verify(
        self,
        payload: bytes,
        signature: Any,
        timestamp: Any,
        *,
        max_age_seconds: int = 300,
    ) -> bool:
        """Verify a signed payload using constant-time comparison.

        Args:
            payload: The original payload bytes.
            signature: The hex signature to verify against.
            timestamp: The timestamp embedded in the signature.
            max_age_seconds: Reject requests older than this many seconds.

        Returns:
            True if the signature is valid and not stale; False otherwise.
        """
        if not isinstance(signature, str):
            return False
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError):
            return False
        now = int(time.time())
        if abs(now - timestamp_int) > max_age_seconds:
            return False
        expected = self._compute_signature(payload, timestamp_int)
        return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

_DEFAULT_BASE_DELAY = 1.0  # seconds


class WebhookDeliverer:
    """Delivers signed webhook events via HTTP POST with exponential backoff.

    Args:
        signer: :class:`WebhookSigner` instance used to sign each delivery.
        http_client: Optional pre-configured ``httpx.AsyncClient``. If None,
            a new client is created per :meth:`deliver` call.
        timeout: Per-request timeout in seconds.
        max_attempts: Maximum number of delivery attempts (including the first).
    """

    def __init__(
        self,
        signer: WebhookSigner,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        max_attempts: int = 5,
    ) -> None:
        self._signer = signer
        self._http_client = http_client
        self._timeout = timeout
        self._max_attempts = max_attempts

    async def deliver(self, *, url: str, event: dict) -> dict[str, Any]:
        """Sign and POST *event* to *url* with retry on failure.

        Retries on HTTP 5xx responses and network/timeout errors with
        exponential backoff (base 1 s, doubling per attempt).

        Args:
            url: Destination URL.
            event: Event dictionary. Must contain a ``"type"`` key.

        Returns:
            A dict with keys ``status_code``, ``latency_ms``, ``attempts``,
            ``delivered_at``.

        Raises:
            WebhookDeliveryError: After all attempts are exhausted.
        """
        payload_bytes = json.dumps(event, separators=(",", ":")).encode("utf-8")
        event_type = str(event.get("type", "unknown"))

        last_error: Exception | None = None
        attempt = 0

        while attempt < self._max_attempts:
            attempt += 1
            sig, ts = self._signer.sign(payload_bytes)
            headers = {
                "Content-Type": "application/json",
                "WorldFork-Signature": f"t={ts},v1={sig}",
                "WorldFork-Event": event_type,
            }

            start = time.monotonic()
            try:
                if self._http_client is not None:
                    resp = await self._http_client.post(
                        url,
                        content=payload_bytes,
                        headers=headers,
                        timeout=self._timeout,
                    )
                else:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            url,
                            content=payload_bytes,
                            headers=headers,
                            timeout=self._timeout,
                        )
                latency_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code < 500:
                    # 2xx/3xx/4xx — not a server-side transient error; don't retry
                    return {
                        "status_code": resp.status_code,
                        "latency_ms": latency_ms,
                        "attempts": attempt,
                        "delivered_at": now_utc().isoformat(),
                    }

                # 5xx — retry
                last_error = WebhookDeliveryError(
                    f"Server error {resp.status_code} on attempt {attempt}"
                )

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                latency_ms = int((time.monotonic() - start) * 1000)
                last_error = exc

            # Exponential backoff before next attempt
            if attempt < self._max_attempts:
                backoff = _DEFAULT_BASE_DELAY * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

        raise WebhookDeliveryError(
            f"Webhook delivery to {url!r} failed after {self._max_attempts} attempt(s)"
        ) from last_error
