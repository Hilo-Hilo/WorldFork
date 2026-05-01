"""Compatibility wrapper for graph API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.sociology.graph_routes")
