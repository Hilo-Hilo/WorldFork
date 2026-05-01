"""Compatibility wrapper for the jobs domain queue constants/helpers."""

from app.domains._compat import alias_module

alias_module(__name__, "app.domains.jobs.queues")
