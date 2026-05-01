"""Compatibility wrapper for legacy /api/multiverse routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.legacy.multiverse")
