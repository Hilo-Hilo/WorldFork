"""Compatibility wrapper for multiverse runtime configuration."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.multiverse.runtime_config")
