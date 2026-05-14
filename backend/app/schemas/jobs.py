"""
Job queue schemas: JobEnvelope, JobStatus.
Import-free of backend.app.models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobType = Literal[
    "initialize_big_bang",
    "simulate_universe_tick",
    "actor_deliberation_call",
    "execute_due_events",
    "social_propagation",
    "sociology_update",
    "god_agent_review",
    "branch_universe",
    "build_review_index",
    "export_run",
    "apply_tick_results",
    "aggregate_run_results",
    "evaluate_endpoint_ledger",
    "force_deviation",
]

AuditedLLMRouteType = Literal[
    "initializer_chunk_extractor",
    "initializer_agent",
    "god_agent",
    "cohort_agent",
    "hero_agent",
    "event_summary",
    "predicate_extractor",
    "predicate_resolver",
    "single_report_agent",
    "final_report_agent",
    "report_agent",
    "endpoint_ledger",
]

ModelRoutingJobType = JobType | AuditedLLMRouteType

JobPriority = Literal["p0", "p1", "p2", "p3", "dead_letter"]


class JobEnvelope(BaseModel):
    """
    Envelope for every async job — carries routing, idempotency, and payload.
    Uses extra="forbid" because this is a closed contract consumed by workers.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_type: JobType
    priority: JobPriority
    run_id: str
    universe_id: str | None = None
    tick: int | None = Field(default=None, ge=0)
    attempt_number: int = Field(..., ge=0)
    idempotency_key: str
    artifact_path: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    enqueued_at: datetime | None = None

    def redis_key(self) -> str:
        """
        Return the Redis SETNX key for idempotency deduplication.

        Keyed on idempotency_key (not attempt_number) so retries on the same
        logical operation share a key and don't re-execute committed side effects.
        """
        return f"wf:job:idem:{self.idempotency_key}"


class JobStatus(BaseModel):
    """Per-job status record returned by the jobs API."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: Literal[
        "queued",
        "running",
        "paused",
        "interrupt_requested",
        "interrupted",
        "cancelled",
        "succeeded",
        "completed",
        "failed",
        "retried",
        "dead",
        "dead_lettered",
    ]
    attempt_number: int = Field(..., ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_summary: dict[str, Any] | None = None
