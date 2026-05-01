"""Compatibility wrapper for the tick bundle domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.tick.tick_bundles")
