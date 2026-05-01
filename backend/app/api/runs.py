"""Compatibility wrapper for legacy /api/runs routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.legacy.runs")
