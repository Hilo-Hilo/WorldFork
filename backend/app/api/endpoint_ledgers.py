"""Compatibility wrapper for endpoint-ledger API routes."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.endpoint_ledger.routes")
