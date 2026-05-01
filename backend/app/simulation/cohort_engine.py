"""Compatibility wrapper for cohort split/merge/emergence logic."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.sociology.cohort_engine")
