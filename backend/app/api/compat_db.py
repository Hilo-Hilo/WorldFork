"""Compatibility wrapper for runtime DB introspection helpers."""

from app.domains._compat import alias_module

alias_module(__name__, "app.db.introspection")
