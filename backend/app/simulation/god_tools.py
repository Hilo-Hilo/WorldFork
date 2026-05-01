"""Compatibility wrapper for governance tool execution."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.governance.god_tools")
