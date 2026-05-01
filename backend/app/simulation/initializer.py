"""Compatibility wrapper for the Big Bang initialization domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.initializer")
