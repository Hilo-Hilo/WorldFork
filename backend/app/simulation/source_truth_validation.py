"""Compatibility wrapper for source-of-truth initialization validation."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.big_bang.source_truth_validation")
