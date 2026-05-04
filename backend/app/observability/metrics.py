"""Prometheus metrics for WorldFork.

All metrics are registered in the default Prometheus registry.
Helper functions are provided so callers never need to import the metric
objects directly.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Queue / worker metrics
# ---------------------------------------------------------------------------

wf_queue_depth = Gauge(
    "wf_queue_depth",
    "Queue depth",
    ["queue"],
)

wf_active_workers = Gauge(
    "wf_active_workers",
    "Active workers",
)

# ---------------------------------------------------------------------------
# Job latency
# ---------------------------------------------------------------------------

wf_job_latency_seconds = Histogram(
    "wf_job_latency_seconds",
    "Job latency",
    ["job_type"],
)

# ---------------------------------------------------------------------------
# Provider metrics
# ---------------------------------------------------------------------------

wf_provider_latency_seconds = Histogram(
    "wf_provider_latency_seconds",
    "Provider latency",
    ["provider", "model"],
)

wf_provider_requests_total = Counter(
    "wf_provider_requests_total",
    "Provider requests",
    ["provider", "model", "status"],
)

wf_provider_tokens_total = Counter(
    "wf_provider_tokens_total",
    "Provider tokens",
    ["provider", "model", "direction"],  # direction: prompt | completion
)

wf_provider_retries_total = Counter(
    "wf_provider_retries_total",
    "Provider retries",
    ["provider", "reason"],
)

wf_provider_errors_total = Counter(
    "wf_provider_errors_total",
    "Provider errors",
    ["provider", "code"],
)

# ---------------------------------------------------------------------------
# Simulation / branching state
# ---------------------------------------------------------------------------

wf_branch_budget_pct = Gauge(
    "wf_branch_budget_pct",
    "Branch budget % used",
)

wf_active_universes = Gauge(
    "wf_active_universes",
    "Active universes",
)

wf_candidate_branches = Gauge(
    "wf_candidate_branches",
    "Candidate branches",
)

wf_webhook_delivery_total = Counter(
    "wf_webhook_delivery_total",
    "Webhook delivery",
    ["status"],
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def observe_provider_call(
    provider: str,
    model: str,
    status: str,
    latency_seconds: float,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Record a single provider call across all relevant metrics.

    Args:
        provider: Provider name (e.g. ``"openrouter"``).
        model: Model identifier (e.g. ``"openai/gpt-4o"``).
        status: Outcome string (e.g. ``"success"``, ``"error"``, ``"timeout"``).
        latency_seconds: End-to-end latency of the call.
        prompt_tokens: Number of prompt tokens consumed.
        completion_tokens: Number of completion tokens consumed.
    """
    wf_provider_requests_total.labels(
        provider=provider, model=model, status=status
    ).inc()
    wf_provider_latency_seconds.labels(provider=provider, model=model).observe(
        latency_seconds
    )
    if prompt_tokens > 0:
        wf_provider_tokens_total.labels(
            provider=provider, model=model, direction="prompt"
        ).inc(prompt_tokens)
    if completion_tokens > 0:
        wf_provider_tokens_total.labels(
            provider=provider, model=model, direction="completion"
        ).inc(completion_tokens)


def observe_job_completion(job_type: str, latency_seconds: float) -> None:
    """Record a completed job's latency.

    Args:
        job_type: Celery job/task name (e.g. ``"simulate_universe_tick"``).
        latency_seconds: Wall-clock duration of the job.
    """
    wf_job_latency_seconds.labels(job_type=job_type).observe(latency_seconds)


def update_queue_depth(queue: str, depth: int) -> None:
    """Update the current depth for a named queue.

    Args:
        queue: Queue name (``"p0"``, ``"p1"``, ``"p2"``, ``"p3"``).
        depth: Current number of pending messages.
    """
    wf_queue_depth.labels(queue=queue).set(depth)
