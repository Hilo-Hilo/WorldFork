"""Security utilities — token verification and WebSocket credential extraction."""
from __future__ import annotations

from fastapi import WebSocket


def verify_token(token: str) -> bool:
    """Verify a session/bearer token.

    TODO: Replace with real JWT/session check.
    Currently accepts any non-empty token string so development works
    without a full auth stack.  Never log the token value.
    """
    return bool(token and token.strip())


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
