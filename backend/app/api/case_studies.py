"""Compatibility wrapper for case-study API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.case_studies_routes")
