"""Compatibility wrapper for the event domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.event.event_engine")
