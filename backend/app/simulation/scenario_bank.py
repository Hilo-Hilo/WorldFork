"""Compatibility wrapper for the scenario bank domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.scenario_bank")
