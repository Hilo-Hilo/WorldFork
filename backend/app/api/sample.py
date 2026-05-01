"""Compatibility wrapper for sample-run API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.sample_routes")
