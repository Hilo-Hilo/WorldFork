"""Unit tests for backend.app.observability.metrics and the /metrics endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.observability.metrics import (
    observe_provider_call,
    observe_job_completion,
    update_queue_depth,
    wf_provider_requests_total,
    wf_provider_tokens_total,
    wf_job_latency_seconds,
    wf_queue_depth,
)


# ---------------------------------------------------------------------------
# Helper: read a counter's value by label
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels) -> float:
    """Return the current value of a labeled counter."""
    return counter.labels(**labels)._value.get()


def _histogram_count(histogram, **labels) -> int:
    """Return the sample count of a labeled histogram."""
    from prometheus_client import REGISTRY

    # Build metric name from the metric object
    metric_name = histogram._name + "_count"
    value = REGISTRY.get_sample_value(metric_name, labels)
    return int(value) if value is not None else 0


def _gauge_value(gauge, **labels) -> float:
    """Return the current value of a labeled gauge."""
    return gauge.labels(**labels)._value.get()


# ---------------------------------------------------------------------------
# Metric helper tests
# ---------------------------------------------------------------------------


class TestObserveProviderCall:
    def test_increments_request_counter(self) -> None:
        before = _counter_value(
            wf_provider_requests_total,
            provider="openrouter",
            model="openai/gpt-4o",
            status="success",
        )
        observe_provider_call(
            "openrouter", "openai/gpt-4o", "success", 0.5, 100, 50
        )
        after = _counter_value(
            wf_provider_requests_total,
            provider="openrouter",
            model="openai/gpt-4o",
            status="success",
        )
        assert after - before == 1.0

    def test_increments_prompt_token_counter(self) -> None:
        before = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o",
            direction="prompt",
        )
        observe_provider_call(
            "openrouter", "openai/gpt-4o", "success", 0.1, 200, 0
        )
        after = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o",
            direction="prompt",
        )
        assert after - before == 200.0

    def test_increments_completion_token_counter(self) -> None:
        before = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o",
            direction="completion",
        )
        observe_provider_call(
            "openrouter", "openai/gpt-4o", "success", 0.1, 0, 75
        )
        after = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o",
            direction="completion",
        )
        assert after - before == 75.0

    def test_zero_tokens_not_incremented(self) -> None:
        before_prompt = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o-mini",
            direction="prompt",
        )
        observe_provider_call(
            "openrouter", "openai/gpt-4o-mini", "error", 0.05, 0, 0
        )
        after_prompt = _counter_value(
            wf_provider_tokens_total,
            provider="openrouter",
            model="openai/gpt-4o-mini",
            direction="prompt",
        )
        assert after_prompt == before_prompt


class TestObserveJobCompletion:
    def test_records_job_latency(self) -> None:
        before_count = _histogram_count(
            wf_job_latency_seconds, job_type="simulate_universe_tick"
        )
        observe_job_completion("simulate_universe_tick", 2.5)
        after_count = _histogram_count(
            wf_job_latency_seconds, job_type="simulate_universe_tick"
        )
        assert after_count - before_count == 1


class TestUpdateQueueDepth:
    def test_sets_gauge_value(self) -> None:
        update_queue_depth("p0", 42)
        assert _gauge_value(wf_queue_depth, queue="p0") == 42.0

    def test_updates_to_new_value(self) -> None:
        update_queue_depth("p1", 10)
        update_queue_depth("p1", 5)
        assert _gauge_value(wf_queue_depth, queue="p1") == 5.0


# ---------------------------------------------------------------------------
# /metrics HTTP endpoint test
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        from backend.app.main import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type(self) -> None:
        from backend.app.main import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_metrics_includes_wf_provider_requests_total(self) -> None:
        from backend.app.main import create_app

        # Emit a counter so it appears in the output
        observe_provider_call("openrouter", "openai/gpt-4o", "success", 0.1, 1, 1)

        app = create_app()
        client = TestClient(app)
        resp = client.get("/metrics")
        assert "wf_provider_requests_total" in resp.text

    def test_healthz_still_works(self) -> None:
        from backend.app.main import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_readyz_reports_dependency_state(self) -> None:
        from backend.app.main import create_app

        app = create_app()
        client = TestClient(app)
        resp = client.get("/readyz")
        assert resp.status_code in {200, 503}
        data = resp.json()
        assert "ok" in data
        assert "checks" in data
        assert {"database", "redis", "openrouter", "openai-codex"}.issubset(data["checks"])
