"""Compatibility wrapper for legacy DB helpers."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.legacy.compat_db")
