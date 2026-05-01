"""Compatibility wrapper for artifact API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.artifacts.routes")
