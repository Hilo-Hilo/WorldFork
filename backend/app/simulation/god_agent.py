"""Compatibility wrapper for the governance domain."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.governance.god_agent")
