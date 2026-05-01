"""Compatibility wrapper for the multiverse branching domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.multiverse.branch_engine")
