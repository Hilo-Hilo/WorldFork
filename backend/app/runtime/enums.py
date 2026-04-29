from __future__ import annotations

from enum import StrEnum


class NodeKind(StrEnum):
    """Logical node types for the tick runtime graph."""

    PROMPT_ASSEMBLY = "prompt_assembly"
    COHORT_DECISION = "cohort_decision"
    HERO_DECISION = "hero_decision"
    TOOL_CALL = "tool_call"
    VALIDATION = "validation"
    BARRIER = "barrier"
    INTERRUPT_CHECK = "interrupt_check"
    EVENT_GENERATION = "event_generation"
    SOCIOLOGY_UPDATE = "sociology_update"
    GRAPH_UPDATE = "graph_update"
    GOD_REVIEW = "god_review"
    TICK_SUMMARY = "tick_summary"
    STATE_COMMIT = "state_commit"
