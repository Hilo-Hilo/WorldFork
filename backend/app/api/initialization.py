"""Compatibility wrapper for initialization API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.initialization_routes")
