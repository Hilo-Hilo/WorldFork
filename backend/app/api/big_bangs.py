"""Compatibility wrapper for Big Bang API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.routes")
