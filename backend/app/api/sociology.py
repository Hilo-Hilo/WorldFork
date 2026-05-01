"""Compatibility wrapper for sociology API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.sociology.routes")
