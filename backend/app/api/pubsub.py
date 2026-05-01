"""Compatibility wrapper for legacy pubsub helpers."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.legacy.pubsub")
