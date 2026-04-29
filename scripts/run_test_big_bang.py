"""Run the long-form Atlas Resilience Crisis sample against WorldFork.

This is a cheap live demonstration harness: it reads ``examples/test-big-bang.md``
as the scenario dossier, creates a Big Bang, runs one root tick, creates one
manual branch, runs one child tick, and verifies runtime/audit artifacts.

Run:
    uv run python -m scripts.run_test_big_bang
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = ROOT / "examples" / "test-big-bang.md"
DEFAULT_BASE_URL = os.environ.get("WORLDFORK_API_URL", "http://127.0.0.1:8003")
GEMINI_MODEL = "google/gemini-3.1-flash-lite-preview"


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

from app.core.config import get_settings  # noqa: E402
from app.db import models  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.simulation.branch_engine import create_branch  # noqa: E402


class SampleFailure(AssertionError):
    """Raised when the sample harness fails a required check."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SampleFailure(message)
    print(f"[pass] {message}")


def request(
    client: httpx.Client,
    base_url: str,
    method: str,
    path: str,
    *,
    expected: int | set[int] = 200,
    json: dict[str, Any] | None = None,
) -> Any:
    expected_set = expected if isinstance(expected, set) else {expected}
    response = client.request(method, f"{base_url}{path}", json=json)
    if response.status_code not in expected_set:
        raise SampleFailure(f"{method} {path} -> {response.status_code}: {response.text[:1200]}")
    if response.content:
        return response.json()
    return None


def wait_for_ready(client: httpx.Client, base_url: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(60):
        try:
            payload = request(client, base_url, "GET", "/readyz")
            if payload.get("ok"):
                return payload
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
        time.sleep(1)
    raise SampleFailure(f"API did not become ready: {last_error}")


def sample_payload(scenario_text: str) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Atlas Resilience Crisis sample {suffix}",
        "description": "Long-form branching and multiverse demonstration using the test Big Bang dossier.",
        "scenario_text": scenario_text,
        "scenario_input": {
            "premise": "A fictional 40-million-person coastal megaregion enters a cascading resilience crisis.",
            "setting": "fictional near-future coastal megaregion",
            "sample_file": "examples/test-big-bang.md",
            "sample_profile": "long_horizon_branching_demo",
        },
        "use_initializer_agent": False,
        "simulation_config": {
            "max_ticks": 180,
            "tick_duration_minutes": 720,
            "sample_smoke_ticks": 2,
        },
        "branch_policy": {
            "max_branch_depth": 5,
            "max_active_multiverses": 24,
            "max_branches_per_tick": 2,
            "branch_score_threshold": 0.55,
            "idle_termination_ticks": 12,
        },
        "actors": [
            {"name": "Atlas Regional Council", "actor_type": "institution", "goals": ["legitimacy", "stability"]},
            {"name": "Water Authority", "actor_type": "institution", "goals": ["pressure reliability", "allocation fairness"]},
            {"name": "Grid Operations Desk", "actor_type": "institution", "goals": ["load stability", "critical services"]},
            {"name": "Public Health Network", "actor_type": "institution", "goals": ["care continuity", "trusted communication"]},
            {"name": "School Shelter Office", "actor_type": "institution", "goals": ["shelter capacity", "student safety"]},
            {"name": "Port Logistics Board", "actor_type": "institution", "goals": ["supply continuity", "priority exemptions"]},
            {"name": "Civic Data Office", "actor_type": "institution", "goals": ["ACCS coordination", "public confidence"]},
            {"name": "Emergency Court Panel", "actor_type": "institution", "goals": ["due process", "emergency legality"]},
            {"name": "Dr. Ilya Sato", "actor_type": "hero", "goals": ["health protection", "honest communication"]},
            {"name": "Mira Venn", "actor_type": "hero", "goals": ["mutual aid", "neighborhood trust"]},
            {"name": "Lena Ortiz", "actor_type": "hero", "goals": ["AI accountability", "public audit"]},
            {"name": "Tomas Reed", "actor_type": "hero", "goals": ["worker safety", "field judgment"]},
            {"name": "Arun Bell", "actor_type": "hero", "goals": ["truthful reporting", "public scrutiny"]},
            {"name": "Nia Ko", "actor_type": "hero", "goals": ["shelter order", "family safety"]},
        ],
        "cohorts": [
            {"name": "Low Pressure District Residents", "state": {"trust": 0.32, "anger": 0.74, "represented_population": 5200000}},
            {"name": "Hill Zone Households", "state": {"trust": 0.62, "scarcity_anxiety": 0.45, "represented_population": 1800000}},
            {"name": "Clinic-Dependent Patients", "state": {"risk": 0.82, "clinic_trust": 0.88, "represented_population": 600000}},
            {"name": "Utility Workers", "state": {"compliance": 0.64, "fatigue": 0.71, "represented_population": 42000}},
            {"name": "Teachers and Shelter Staff", "state": {"capacity": 0.58, "safety_concern": 0.69, "represented_population": 240000}},
            {"name": "Migrant Families", "state": {"housing_need": 0.86, "public_support": 0.48, "represented_population": 310000}},
            {"name": "Small Businesses", "state": {"closure_risk": 0.66, "policy_trust": 0.41, "represented_population": 900000}},
            {"name": "Port and Warehouse Workers", "state": {"heat_risk": 0.63, "leverage": 0.76, "represented_population": 180000}},
            {"name": "Civic Tech Optimists", "state": {"ACCS_trust": 0.78, "transparency_need": 0.61, "represented_population": 2500000}},
            {"name": "Civic Tech Skeptics", "state": {"ACCS_trust": 0.18, "appeal_demand": 0.83, "represented_population": 3300000}},
            {"name": "Youth Climate Network", "state": {"mobilization": 0.67, "legitimacy_pressure": 0.72, "represented_population": 1200000}},
            {"name": "Regional Stability Voters", "state": {"order_preference": 0.79, "trust": 0.55, "represented_population": 8200000}},
        ],
        "heroes": [
            {"name": "Dr. Ilya Sato", "definition": {"role": "clinic coordinator"}, "state": {"trust": 0.88, "burnout": 0.38}},
            {"name": "Mira Venn", "definition": {"role": "mutual aid organizer"}, "state": {"network_capacity": 0.64, "official_trust": 0.27}},
            {"name": "Lena Ortiz", "definition": {"role": "civic data auditor"}, "state": {"evidence_access": 0.72, "personal_risk": 0.55}},
            {"name": "Tomas Reed", "definition": {"role": "utility crew steward"}, "state": {"crew_trust": 0.81, "management_trust": 0.34}},
            {"name": "Arun Bell", "definition": {"role": "local reporter"}, "state": {"source_network": 0.73, "deadline_pressure": 0.68}},
            {"name": "Nia Ko", "definition": {"role": "school principal"}, "state": {"shelter_capacity": 0.57, "community_trust": 0.69}},
        ],
    }


def assert_config_uses_gemini() -> None:
    settings = get_settings()
    check(settings.default_llm_provider == "openrouter", "default provider is OpenRouter")
    model_slots = {
        "default": settings.default_model,
        "fallback": settings.fallback_model,
        "initializer": settings.initializer_agent_model,
        "god": settings.god_agent_model,
        "cohort": settings.cohort_agent_model,
        "hero": settings.hero_agent_model,
        "event_summary": settings.event_summary_model,
        "report": settings.report_agent_model,
    }
    for label, model in model_slots.items():
        check(model == GEMINI_MODEL, f"{label} model is Gemini 3.1 Flash Lite")


def assert_gemini_only(big_bang_id: str) -> int:
    db = SessionLocal()
    try:
        calls = db.scalars(
            select(models.LLMCall)
            .where(models.LLMCall.big_bang_id == big_bang_id)
            .order_by(models.LLMCall.created_at.asc())
        ).all()
        check(bool(calls), "sample produced audited LLM calls")
        non_gemini = [call.model for call in calls if GEMINI_MODEL not in str(call.model)]
        if non_gemini:
            raise SampleFailure(f"non-Gemini models were used: {non_gemini}")
        print(f"[pass] all {len(calls)} audited LLM calls used {GEMINI_MODEL}")
        return len(calls)
    finally:
        db.close()


def validate_runtime(runtime: dict[str, Any]) -> None:
    executions = runtime.get("executions") or []
    check(len(executions) >= 1, "runtime endpoint returned at least one execution")
    execution = executions[-1]
    check(execution.get("status") == "succeeded", "runtime execution succeeded")
    checkpoints = execution.get("checkpoints") or []
    keys = {item["checkpoint_key"]: item["status"] for item in checkpoints}
    for key in ("event_generation", "sociology_update", "graph_update", "god_review", "tick_summary"):
        check(keys.get(key) == "complete", f"checkpoint {key} completed")
    tool_keys = [key for key in keys if key.startswith("tool_call:")]
    check(bool(tool_keys), "at least one tool-call checkpoint completed")
    check(all(keys[key] == "complete" for key in tool_keys), "all tool-call checkpoints completed")


def record_manual_transparency_branch(parent_multiverse_id: str, tick_snapshot_id: str) -> str:
    db = SessionLocal()
    try:
        parent = db.get(models.Multiverse, parent_multiverse_id)
        tick = db.get(models.TickSnapshot, tick_snapshot_id)
        if parent is None or tick is None:
            raise SampleFailure("manual branch could not load parent/tick")
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
        reason = "Atlas sample branch: publish live rationing dashboard and invite clinic plus mutual-aid briefings."
        child = create_branch(
            db,
            parent=parent,
            fork_tick_index=tick.tick_index,
            reason=reason,
            idempotency_key=f"atlas-sample:manual-transparency:{tick.id}:{uuid.uuid4().hex}",
        )
        intervention = models.Intervention(
            big_bang_id=parent.big_bang_id,
            multiverse_id=parent.id,
            tick_execution_id=execution.id if execution else None,
            tick_snapshot_id=tick.id,
            checkpoint_id=checkpoint.id if checkpoint else None,
            intervention_type="manual_branch",
            actor="run_test_big_bang",
            reason=reason,
            status="applied",
            payload={
                "child_multiverse_id": str(child.id),
                "child_label": child.ui_label,
                "branch_choice": "full_transparency_dashboard",
            },
            provenance={"script": "scripts/run_test_big_bang.py", "sample": "atlas_resilience_crisis"},
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
                body={
                    "intervention_type": "manual_branch",
                    "branch_choice": "full_transparency_dashboard",
                    "child_multiverse_id": str(child.id),
                },
            )
        )
        db.commit()
        print(f"[pass] manual transparency branch created {child.ui_label} ({child.id})")
        return str(child.id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_sample(args: argparse.Namespace) -> None:
    scenario_path = Path(args.scenario_file).resolve()
    scenario_text = scenario_path.read_text()
    check(len(scenario_text) > 10_000, "test-big-bang.md is a long-form scenario dossier")
    assert_config_uses_gemini()

    base_url = str(args.base_url).rstrip("/")
    with httpx.Client(timeout=args.timeout) as client:
        ready = wait_for_ready(client, base_url)
        check(ready["checks"]["openrouter"], "readyz reports OpenRouter configured")

        big_bang = request(client, base_url, "POST", "/api/big-bangs", expected=201, json=sample_payload(scenario_text))
        big_bang_id = big_bang["id"]
        print(f"[info] Big Bang: {big_bang_id}")
        check(big_bang["status"] == "draft", "Atlas sample Big Bang created")

        multiverses = request(client, base_url, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
        check(len(multiverses) == 1, "root multiverse created")
        root_multiverse_id = multiverses[0]["id"]
        print(f"[info] Root multiverse: {root_multiverse_id}")

        resumed = request(client, base_url, "POST", f"/api/big-bangs/{big_bang_id}/resume")
        check(resumed["status"] == "running", "Atlas sample Big Bang resumed")

        root_tick = request(
            client,
            base_url,
            "POST",
            f"/api/multiverses/{root_multiverse_id}/simulate-next-tick",
            json={"idempotency_key": f"atlas-root-{root_multiverse_id}-t1"},
        )
        check(root_tick["status"] == "final", "root Atlas tick completed")
        validate_runtime(request(client, base_url, "GET", f"/api/ticks/{root_tick['id']}/runtime"))
        check(request(client, base_url, "GET", f"/api/ticks/{root_tick['id']}/god-review")["decision"], "root God review exists")
        check(len(request(client, base_url, "GET", f"/api/ticks/{root_tick['id']}/social")["posts"]) >= 1, "root social posts exist")
        check(len(request(client, base_url, "GET", f"/api/ticks/{root_tick['id']}/graph-deltas")) >= 1, "root graph deltas exist")
        check(len(request(client, base_url, "GET", f"/api/ticks/{root_tick['id']}/sociology-signals")) >= 1, "root sociology signals exist")
        assert_gemini_only(big_bang_id)

        child_multiverse_id = record_manual_transparency_branch(root_multiverse_id, root_tick["id"])
        lineage = request(client, base_url, "GET", f"/api/multiverses/{child_multiverse_id}/lineage")
        check(len(lineage["edges"]) >= 1, "manual branch lineage edge is visible")

        child_tick = request(
            client,
            base_url,
            "POST",
            f"/api/multiverses/{child_multiverse_id}/simulate-next-tick",
            json={"idempotency_key": f"atlas-child-{child_multiverse_id}-t1"},
        )
        check(child_tick["status"] == "final", "child Atlas branch tick completed")
        validate_runtime(request(client, base_url, "GET", f"/api/ticks/{child_tick['id']}/runtime"))
        request(client, base_url, "POST", f"/api/multiverses/{root_multiverse_id}/terminate")
        request(client, base_url, "POST", f"/api/multiverses/{child_multiverse_id}/terminate")
        report = request(
            client,
            base_url,
            "POST",
            f"/api/big-bangs/{big_bang_id}/reports/final",
            json={
                "title": "Atlas Resilience Crisis Sample Report",
                "summary": "Generated by scripts/run_test_big_bang.py after root and transparency branch ticks.",
            },
        )
        check(report["markdown_artifact_id"], "final Atlas sample markdown report generated")
        check(report["pdf_artifact_id"], "final Atlas sample PDF report generated")
        call_count = assert_gemini_only(big_bang_id)

    print("\n== TEST BIG BANG SAMPLE OK ==")
    print(f"big_bang_id={big_bang_id}")
    print(f"root_multiverse_id={root_multiverse_id}")
    print(f"child_multiverse_id={child_multiverse_id}")
    print(f"audited_llm_calls={call_count}")
    print(f"model={GEMINI_MODEL}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Atlas test Big Bang sample.")
    parser.add_argument("--scenario-file", default=str(DEFAULT_SCENARIO), help="Markdown scenario dossier.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="WorldFork backend base URL.")
    parser.add_argument("--timeout", type=float, default=240.0, help="HTTP timeout in seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_sample(args)
    except Exception as exc:
        print(f"\nTEST BIG BANG SAMPLE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
