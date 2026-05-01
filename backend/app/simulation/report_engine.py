"""Compatibility wrapper for report domain services."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.report.engine")
