"""Compatibility wrapper for the sociology domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.sociology.sociology_engine")
