"""Compatibility wrapper for endpoint-ledger domain services."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.endpoint_ledger.service")
