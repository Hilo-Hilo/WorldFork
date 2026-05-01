"""Compatibility wrapper for scenario-bank API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.scenario_bank_routes")
