"""Compatibility wrapper for emotion-observability API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.sociology.emotion_routes")
