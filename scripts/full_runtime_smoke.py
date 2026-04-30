"""Full live runtime smoke against the local WorldFork API.

This script intentionally uses the running API plus the same Postgres database
for direct verification of runtime metadata that is not yet exposed through a
dedicated public intervention endpoint.

Run:
    worldfork smoke live
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

GEMINI_MODEL = "google/gemini-3.1-flash-lite-preview"
BASE_URL = os.environ.get("WORLDFORK_API_URL", "http://127.0.0.1:8003")


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.simulation.branch_engine import create_branch  # noqa: E402

from backend.app.models.settings import GlobalSettingModel  # noqa: E402


class SmokeFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)
    print(f"[pass] {message}")


def request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected: int | set[int] = 200,
    json: dict[str, Any] | None = None,
) -> Any:
    expected_set = expected if isinstance(expected, set) else {expected}
    response = client.request(method, f"{BASE_URL}{path}", json=json)
    if response.status_code not in expected_set:
        raise SmokeFailure(f"{method} {path} -> {response.status_code}: {response.text[:1000]}")
    if response.content:
        return response.json()
    return None


def wait_for_ready(client: httpx.Client) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            payload = request(client, "GET", "/readyz")
            if payload.get("ok"):
                return payload
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
        time.sleep(1)
    raise SmokeFailure(f"API did not become ready: {last_error}")


def assert_gemini_only(big_bang_id: str) -> None:
    db = SessionLocal()
    try:
        calls = db.scalars(
            select(models.LLMCall)
            .where(models.LLMCall.big_bang_id == big_bang_id)
            .order_by(models.LLMCall.created_at.asc())
        ).all()
        check(bool(calls), "live simulation produced audited LLM calls")
        non_gemini = [call.model for call in calls if GEMINI_MODEL not in str(call.model)]
        if non_gemini:
            raise SmokeFailure(f"non-Gemini models were used: {non_gemini}")
        print(f"[pass] all {len(calls)} audited LLM calls used {GEMINI_MODEL}")
    finally:
        db.close()


def delete_smoke_settings_row() -> None:
    db = SessionLocal()
    try:
        row = db.get(GlobalSettingModel, "default")
        if row is not None and (row.payload or {}).get("smoke_setting_change") is True:
            db.delete(row)
            db.commit()
            print("[pass] temporary smoke settings row removed")
    finally:
        db.close()


def record_manual_branch_intervention(
    *,
    parent_multiverse_id: str,
    tick_snapshot_id: str,
) -> str:
    db = SessionLocal()
    try:
        parent = db.get(models.Multiverse, parent_multiverse_id)
        tick = db.get(models.TickSnapshot, tick_snapshot_id)
        if parent is None or tick is None:
            raise SmokeFailure("manual intervention could not load parent/tick")
        execution = db.scalar(
            select(models.TickExecution)
            .where(models.TickExecution.tick_snapshot_id == tick.id)
            .order_by(models.TickExecution.created_at.desc())
            .limit(1)
        )
        checkpoint = None
        if execution is not None:
            checkpoint = db.scalar(
                select(models.TickCheckpoint)
                .where(
                    models.TickCheckpoint.tick_execution_id == execution.id,
                    models.TickCheckpoint.checkpoint_key == "tick_summary",
                )
                .limit(1)
            )
        reason = "Automated smoke manual branch intervention"
        child = create_branch(
            db,
            parent=parent,
            fork_tick_index=tick.tick_index,
            reason=reason,
            idempotency_key=f"manual-smoke:{tick.id}:{uuid.uuid4().hex}",
        )
        intervention = models.Intervention(
            big_bang_id=parent.big_bang_id,
            multiverse_id=parent.id,
            tick_execution_id=execution.id if execution else None,
            tick_snapshot_id=tick.id,
            checkpoint_id=checkpoint.id if checkpoint else None,
            intervention_type="manual_branch",
            actor="full_runtime_smoke",
            reason=reason,
            status="applied",
            payload={"child_multiverse_id": str(child.id), "child_label": child.ui_label},
            provenance={"script": "scripts/full_runtime_smoke.py", "mode": "branch_first"},
        )
        db.add(intervention)
        db.flush()
        db.add(
            models.OperationLog(
                big_bang_id=parent.big_bang_id,
                multiverse_id=parent.id,
                tick_execution_id=execution.id if execution else None,
                execution_node_id=None,
                checkpoint_id=checkpoint.id if checkpoint else None,
                intervention_id=intervention.id,
                event_type="manual_intervention_applied",
                level="info",
                body={"intervention_type": "manual_branch", "child_multiverse_id": str(child.id)},
            )
        )
        db.commit()
        print(f"[pass] manual branch intervention created child {child.ui_label} ({child.id})")
        return str(child.id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_synthetic_job(multiverse_id: str, big_bang_id: str) -> str:
    db = SessionLocal()
    try:
        job = models.Job(
            job_type="run_multiverse_tick",
            queue_name="multiverse_ticks",
            status="queued",
            big_bang_id=big_bang_id,
            payload={
                "multiverse_id": multiverse_id,
                "idempotency_key": f"{multiverse_id}:job-smoke:{uuid.uuid4().hex}",
            },
            result={},
            idempotency_key=f"full-runtime-smoke-job:{uuid.uuid4().hex}",
            retryable=True,
        )
        db.add(job)
        db.commit()
        return str(job.id)
    finally:
        db.close()


def delete_paused_control_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if job is not None and job.status == "paused":
            db.delete(job)
            db.commit()
            print("[pass] paused control job cleaned up")
    finally:
        db.close()


def assert_manual_intervention_recorded(child_multiverse_id: str) -> None:
    db = SessionLocal()
    try:
        interventions = db.scalars(
            select(models.Intervention)
            .order_by(models.Intervention.created_at.desc())
            .limit(20)
        ).all()
        intervention = next(
            (
                item
                for item in interventions
                if (item.payload or {}).get("child_multiverse_id") == child_multiverse_id
            ),
            None,
        )
        check(intervention is not None, "manual intervention row was persisted")
        log = db.scalar(
            select(models.OperationLog)
            .where(models.OperationLog.intervention_id == intervention.id)
            .order_by(models.OperationLog.created_at.desc())
            .limit(1)
        )
        check(log is not None, "manual intervention audit log was persisted")
    finally:
        db.close()


def assert_job_runtime_link(job_id: str) -> None:
    db = SessionLocal()
    try:
        execution = db.scalar(
            select(models.TickExecution)
            .where(models.TickExecution.queue_job_id == job_id)
            .order_by(models.TickExecution.created_at.desc())
            .limit(1)
        )
        check(execution is not None, "job-run tick execution is linked to queue_job_id")
        check(execution.status == "succeeded", "job-run tick execution succeeded")
    finally:
        db.close()


def validate_runtime(runtime: dict[str, Any], *, require_tool_call: bool = True) -> None:
    executions = runtime.get("executions") or []
    check(len(executions) >= 1, "runtime endpoint returned at least one execution")
    execution = executions[-1]
    check(execution.get("status") == "succeeded", "runtime execution succeeded")
    checkpoints = execution.get("checkpoints") or []
    keys = {item["checkpoint_key"]: item["status"] for item in checkpoints}
    for key in ("event_generation", "sociology_update", "graph_update", "god_review", "tick_summary"):
        check(keys.get(key) == "complete", f"checkpoint {key} completed")
    if require_tool_call:
        tool_keys = [key for key in keys if key.startswith("tool_call:")]
        check(bool(tool_keys), "at least one tool-call checkpoint completed")
        check(all(keys[key] == "complete" for key in tool_keys), "all tool-call checkpoints completed")
    attempts = execution.get("attempts") or []
    check(len(attempts) >= len(checkpoints), "node attempts were persisted for checkpoints")


def render_pdf(client: httpx.Client, report_version_id: str, label: str) -> dict[str, Any]:
    rendered = request(
        client,
        "POST",
        f"/api/report-versions/{report_version_id}/render",
        json={"format": "pdf"},
    )
    check(rendered.get("artifact_id"), f"{label} PDF artifact rendered on demand")
    return rendered


def terminate_all_multiverses(client: httpx.Client, big_bang_id: str) -> list[dict[str, Any]]:
    multiverses = request(client, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
    for multiverse in multiverses:
        if multiverse.get("status") not in {"completed", "terminated"}:
            request(client, "POST", f"/api/multiverses/{multiverse['id']}/terminate")
    terminated = request(client, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
    check(
        all(item.get("status") in {"completed", "terminated"} for item in terminated),
        "all smoke multiverses are terminal before final report",
    )
    return terminated


def main() -> None:
    settings = get_settings()
    check(settings.default_llm_provider == "openrouter", "default provider is OpenRouter")
    for label, model in {
        "default": settings.default_model,
        "fallback": settings.fallback_model,
        "initializer": settings.initializer_agent_model,
        "god": settings.god_agent_model,
        "cohort": settings.cohort_agent_model,
        "hero": settings.hero_agent_model,
        "event_summary": settings.event_summary_model,
        "report": settings.report_agent_model,
    }.items():
        check(model == GEMINI_MODEL, f"{label} model is Gemini 3.1 Flash Lite")

    original_settings: dict[str, Any] | None = None
    cleanup_settings_row = False
    big_bang_id: str | None = None
    root_multiverse_id: str | None = None
    child_multiverse_id: str | None = None

    with httpx.Client(timeout=180) as client:
        ready = wait_for_ready(client)
        check(ready["checks"]["openrouter"], "readyz reports OpenRouter configured")
        status = request(client, "GET", "/api/agent/status")
        check(status["ok"] and status["data"]["status"] == "ok", "agent status is ok")

        settings_response = request(client, "GET", "/api/settings", expected={200, 404})
        original_settings = settings_response if isinstance(settings_response, dict) else None
        cleanup_settings_row = (
            original_settings is None
            or (original_settings.get("payload") or {}).get("smoke_setting_change") is True
        )
        new_tick_duration = 137
        patched = request(
            client,
            "PATCH",
            "/api/settings",
            json={
                "display_timezone": "America/Los_Angeles",
                "theme": "dark",
                "default_tick_duration_minutes": new_tick_duration,
                "payload": {"smoke_setting_change": True, "value": new_tick_duration},
            },
        )
        check(patched["default_tick_duration_minutes"] == new_tick_duration, "settings PATCH changed tick duration")
        reread = request(client, "GET", "/api/settings")
        check(reread["default_tick_duration_minutes"] == new_tick_duration, "settings GET reflects changed tick duration")
        check(reread["payload"]["smoke_setting_change"] is True, "settings payload mutation persisted")

        create_payload = {
            "name": f"Full runtime smoke {uuid.uuid4().hex[:8]}",
            "description": "Automated full-feature runtime smoke.",
            "scenario_text": (
                "A regional emergency desk and a community clinic coordinate public updates after a short "
                "water-pressure outage. Residents need clarity, trust, and material support."
            ),
            "use_initializer_agent": False,
            "simulation_config": {"max_ticks": 3, "tick_duration_minutes": 60},
            "branch_policy": {
                "max_branch_depth": 2,
                "max_active_multiverses": 6,
                "max_branches_per_tick": 2,
                "branch_score_threshold": 0.95,
            },
            "actors": [
                {"name": "Emergency Desk", "actor_type": "cohort", "description": "Coordinates repair updates."},
                {"name": "Clinic Lead", "actor_type": "hero", "description": "Keeps vulnerable residents informed."},
            ],
            "cohorts": [{"name": "Residents", "state": {"represented_population": 900, "trust": 0.61}}],
            "heroes": [{"name": "Clinic Lead", "definition": {"role": "trusted responder"}, "state": {"readiness": 0.8}}],
        }
        big_bang = request(client, "POST", "/api/big-bangs", expected=201, json=create_payload)
        big_bang_id = big_bang["id"]
        check(big_bang["status"] == "draft", "Big Bang created")

        multiverses = request(client, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
        check(len(multiverses) == 1, "root multiverse created")
        root_multiverse_id = multiverses[0]["id"]

        paused = request(client, "POST", f"/api/big-bangs/{big_bang_id}/pause")
        check(paused["status"] == "paused", "Big Bang pause endpoint works")
        blocked = request(
            client,
            "POST",
            f"/api/multiverses/{root_multiverse_id}/simulate-next-tick",
            expected={409},
            json={"idempotency_key": f"smoke-paused-{root_multiverse_id}-t1"},
        )
        check("paused" in blocked["detail"], "paused Big Bang blocks tick execution")
        resumed = request(client, "POST", f"/api/big-bangs/{big_bang_id}/resume")
        check(resumed["status"] == "running", "Big Bang resume endpoint works")

        tick = request(
            client,
            "POST",
            f"/api/multiverses/{root_multiverse_id}/simulate-next-tick",
            json={"idempotency_key": f"smoke-root-{root_multiverse_id}-t1"},
        )
        check(tick["status"] == "final", "root tick simulation completed")
        root_tick_id = tick["id"]
        runtime = request(client, "GET", f"/api/ticks/{root_tick_id}/runtime")
        validate_runtime(runtime)
        check(request(client, "GET", f"/api/ticks/{root_tick_id}/god-review")["decision"], "God review exists")
        check(len(request(client, "GET", f"/api/ticks/{root_tick_id}/tool-calls")) >= 1, "tool call rows exist")
        check(len(request(client, "GET", f"/api/ticks/{root_tick_id}/social")["posts"]) >= 1, "social posts exist")
        check(len(request(client, "GET", f"/api/ticks/{root_tick_id}/graph-deltas")) >= 1, "graph deltas exist")
        check(len(request(client, "GET", f"/api/ticks/{root_tick_id}/sociology-signals")) >= 1, "sociology signals exist")
        check(len(request(client, "GET", f"/api/ticks/{root_tick_id}/emotion-observability")) >= 1, "emotion observability exists")
        assert_gemini_only(big_bang_id)

        child_multiverse_id = record_manual_branch_intervention(
            parent_multiverse_id=root_multiverse_id,
            tick_snapshot_id=root_tick_id,
        )
        lineage = request(client, "GET", f"/api/multiverses/{child_multiverse_id}/lineage")
        check(len(lineage["edges"]) >= 1, "manual branch lineage edge is visible")
        assert_manual_intervention_recorded(child_multiverse_id)
        child_tick = request(
            client,
            "POST",
            f"/api/multiverses/{child_multiverse_id}/simulate-next-tick",
            json={"idempotency_key": f"smoke-child-{child_multiverse_id}-t1"},
        )
        check(child_tick["status"] == "final", "child branch tick simulation completed")
        validate_runtime(request(client, "GET", f"/api/ticks/{child_tick['id']}/runtime"))
        assert_gemini_only(big_bang_id)

        control_job_id = create_synthetic_job(child_multiverse_id, big_bang_id)
        control_job = request(client, "GET", f"/api/jobs/{control_job_id}")
        check(control_job["status"] == "queued", "synthetic control job is visible")
        paused_job = request(client, "POST", f"/api/jobs/{control_job_id}/pause")
        check(paused_job["status"] == "paused", "job pause endpoint works")
        delete_paused_control_job(control_job_id)

        job_id = create_synthetic_job(root_multiverse_id, big_bang_id)
        job = request(client, "POST", f"/api/jobs/{job_id}/run")
        check(job["status"] == "succeeded", "job run endpoint completed tick")
        check(job["result"].get("tick_snapshot_id"), "job result contains tick snapshot id")
        assert_job_runtime_link(job_id)
        assert_gemini_only(big_bang_id)

        root_report = request(
            client,
            "POST",
            f"/api/multiverses/{root_multiverse_id}/report",
            json={"title": "Root Smoke Report", "summary": "Root timeline report generated by smoke test."},
        )
        check(root_report["markdown_artifact_id"], "root report markdown artifact generated")
        check(root_report["content"]["source"]["multiverse_version"], "root report is bound to a multiverse version")
        render_pdf(client, root_report["id"], "root report")
        child_report = request(
            client,
            "POST",
            f"/api/multiverses/{child_multiverse_id}/report",
            json={"title": "Child Smoke Report", "summary": "Child timeline report generated by smoke test."},
        )
        check(child_report["markdown_artifact_id"], "child report markdown artifact generated")
        check(child_report["content"]["source"]["multiverse_version"], "child report is bound to a multiverse version")
        render_pdf(client, child_report["id"], "child report")
        terminate_all_multiverses(client, big_bang_id)
        final_report = request(
            client,
            "POST",
            f"/api/big-bangs/{big_bang_id}/reports/final",
            json={"title": "Full Smoke Final Report", "summary": "Final report generated by full runtime smoke."},
        )
        check(final_report["markdown_artifact_id"], "final Big Bang report generated after termination")
        check(final_report["content"]["outcome_distribution"], "final Big Bang structured outcome distribution generated")
        render_pdf(client, final_report["id"], "final Big Bang report")

        for log_path in ("/api/logs/audit", "/api/logs/requests", "/api/logs/errors", "/api/logs/webhooks"):
            logs = request(client, "GET", log_path)
            check(isinstance(logs, list), f"{log_path} endpoint responds")

    if cleanup_settings_row:
        delete_smoke_settings_row()
    elif original_settings is not None:
        restore_payload = {
            "display_timezone": original_settings.get("display_timezone", "UTC"),
            "theme": original_settings.get("theme", "system"),
            "default_tick_duration_minutes": original_settings.get("default_tick_duration_minutes", 120),
            "payload": original_settings.get("payload", {}),
        }
        with httpx.Client(timeout=30) as client:
            restored = request(client, "PATCH", "/api/settings", json=restore_payload)
            check(
                restored["default_tick_duration_minutes"] == restore_payload["default_tick_duration_minutes"],
                "settings restored after validation",
            )

    print("\n== FULL RUNTIME SMOKE OK ==")
    print(
        {
            "big_bang_id": big_bang_id,
            "root_multiverse_id": root_multiverse_id,
            "child_multiverse_id": child_multiverse_id,
            "model": GEMINI_MODEL,
            "base_url": BASE_URL,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nFULL RUNTIME SMOKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
