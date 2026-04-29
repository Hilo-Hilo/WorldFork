"""Security utilities — token verification and WebSocket credential extraction."""
from __future__ import annotations

import os
import secrets

from fastapi import WebSocket


def _configured_tokens() -> list[str]:
    tokens: list[str] = []
    for name in (
        "WORLDFORK_SESSION_TOKEN",
        "WORLDFORK_API_TOKEN",
        "WF_SESSION_TOKEN",
        "WF_API_TOKEN",
    ):
        value = os.getenv(name)
        if value and value.strip():
            tokens.append(value.strip())
    return tokens


def verify_token(token: str) -> bool:
    """Verify a session/bearer token.

    When an API/session token is configured, require an exact constant-time
    match.  Local development and tests without configured auth keep the
    previous explicit compatibility mode of accepting any non-empty token.
    Never log the token value.
    """
    candidate = token.strip() if token else ""
    if not candidate:
        return False
    configured = _configured_tokens()
    if not configured:
        return True
    return any(secrets.compare_digest(candidate, expected) for expected in configured)


def cookie_or_token_from_websocket(websocket: WebSocket) -> str | None:
    """Extract a credential from a WebSocket connection.

    Checks (in order):
    1. ``wf_session`` cookie — set by a future first-party session layer.
    2. ``token`` query parameter — for dev/cross-origin WS handshakes where
       some browsers strip cookies.

    Returns the first non-empty value found, or ``None`` if both are absent.
    Never logs the returned value.
    """
    cookie_value: str | None = websocket.cookies.get("wf_session")
    if cookie_value:
        return cookie_value

    token_param: str | None = websocket.query_params.get("token")
    if token_param:
        return token_param

    return None
