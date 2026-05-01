"""Compatibility wrapper for the actor domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.actor.agent_engine")
