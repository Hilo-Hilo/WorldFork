"""Compatibility wrapper for canonical logs API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.logs.routes")
