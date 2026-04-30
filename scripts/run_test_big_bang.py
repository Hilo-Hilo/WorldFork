"""Run the full Atlas Resilience Crisis onboarding demo against WorldFork.

This is not a smoke test. It reads ``examples/test-big-bang.md`` as the
scenario dossier, creates a Big Bang, lets the simulation branch across a
generous set of safety caps, drains all discovered timelines to terminal state,
and produces report-agent summaries across the terminal multiverse outcomes.

Run:
    worldfork demo atlas
"""
from __future__ import annotations

import argparse
import math
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
DEFAULT_ATLAS_TICK_DURATION_MINUTES = 720
DEFAULT_ATLAS_HORIZON_DAYS = 30
RUNNABLE_MULTIVERSE_STATUSES = {"active", "candidate"}
TERMINAL_MULTIVERSE_STATUSES = {"completed", "terminated"}


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
    """Raised when the Atlas onboarding harness fails a required check."""


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
        if "application/json" not in response.headers.get("content-type", ""):
            return response.text
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


def sample_payload(
    scenario_text: str,
    *,
    max_tick_index: int,
    tick_duration_minutes: int,
    max_active_multiverses: int,
    max_branch_depth: int,
    max_branches_per_tick: int,
    branch_score_threshold: float,
    idle_termination_ticks: int,
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    return {
        "name": f"Atlas Resilience Crisis onboarding demo {suffix}",
        "description": "Full branching and reporting demonstration using the Atlas Big Bang dossier.",
        "scenario_text": scenario_text,
        "scenario_input": {
            "premise": "A fictional 40-million-person coastal megaregion enters a cascading resilience crisis.",
            "setting": "fictional near-future coastal megaregion",
            "demo_file": "examples/test-big-bang.md",
            "demo_profile": "full_onboarding_multiverse_demo",
        },
        "use_initializer_agent": False,
        "simulation_config": {
            "max_ticks": max_tick_index,
            "tick_duration_minutes": tick_duration_minutes,
            "atlas_demo_ticks": max_tick_index + 1,
            "atlas_horizon_days": round((max_tick_index * tick_duration_minutes) / 1440, 2),
        },
        "branch_policy": {
            "max_branch_depth": max_branch_depth,
            "max_active_multiverses": max_active_multiverses,
            "max_branches_per_tick": max_branches_per_tick,
            "branch_score_threshold": branch_score_threshold,
            "idle_termination_ticks": idle_termination_ticks,
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
        check(bool(calls), "Atlas onboarding demo produced audited LLM calls")
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


def validate_runtime_or_inheritance(client: httpx.Client, base_url: str, tick: dict[str, Any]) -> None:
    runtime = request(client, base_url, "GET", f"/api/ticks/{tick['id']}/runtime")
    if runtime.get("executions"):
        validate_runtime(runtime)
        return
    inherited_from = (tick.get("final_bundle") or {}).get("inherited_from") or (
        tick.get("provisional_bundle") or {}
    ).get("inherited_from")
    check(bool(inherited_from), "returned branch tick is inherited when no runtime execution exists")


def render_pdf(client: httpx.Client, base_url: str, report_version_id: str) -> dict[str, Any]:
    rendered = request(
        client,
        base_url,
        "POST",
        f"/api/report-versions/{report_version_id}/render",
        json={"format": "pdf"},
    )
    check(rendered.get("artifact_id"), "report PDF artifact rendered on demand")
    return rendered


def run_all_multiverses_to_terminal(
    client: httpx.Client,
    base_url: str,
    big_bang_id: str,
    *,
    max_requests: int,
) -> tuple[list[dict[str, Any]], int]:
    requests_used = 0
    seen_tick_ids: set[str] = set()
    while True:
        multiverses = request(client, base_url, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
        runnable = sorted(
            [item for item in multiverses if item.get("status") in RUNNABLE_MULTIVERSE_STATUSES],
            key=lambda item: item["ui_label"],
        )
        if not runnable:
            break
        if requests_used >= max_requests:
            labels = ", ".join(item["ui_label"] for item in runnable[:8])
            raise SampleFailure(f"completion safety cap exhausted with active multiverses: {labels}")

        made_progress = False
        for multiverse in runnable:
            if requests_used >= max_requests:
                break
            before_status = multiverse["status"]
            tick = request(
                client,
                base_url,
                "POST",
                f"/api/multiverses/{multiverse['id']}/simulate-next-tick",
                json={
                    "idempotency_key": (
                        f"atlas-complete-{multiverse['id']}-{requests_used}-{uuid.uuid4().hex[:8]}"
                    )
                },
            )
            requests_used += 1
            if tick["id"] not in seen_tick_ids:
                seen_tick_ids.add(tick["id"])
                made_progress = True
            after = request(client, base_url, "GET", f"/api/multiverses/{multiverse['id']}")
            if after["status"] != before_status or after["status"] in TERMINAL_MULTIVERSE_STATUSES:
                made_progress = True

        if not made_progress:
            labels = ", ".join(item["ui_label"] for item in runnable[:8])
            raise SampleFailure(f"completion loop made no progress; still active: {labels}")

    terminated = request(client, base_url, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
    check(
        all(item.get("status") in TERMINAL_MULTIVERSE_STATUSES for item in terminated),
        "all discovered Atlas onboarding multiverses reached terminal state before final report",
    )
    check(len(terminated) >= 2, "Atlas onboarding retained the discovered multiverse tree")
    return terminated, requests_used


def generate_multiverse_reports(
    client: httpx.Client,
    base_url: str,
    multiverses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    reports = []
    for multiverse in sorted(multiverses, key=lambda item: item["ui_label"]):
        report = request(
            client,
            base_url,
            "POST",
            f"/api/multiverses/{multiverse['id']}/report",
            json={
                "title": f"Atlas {multiverse['ui_label']} Timeline Report",
                "summary": (
                    f"Structured report for Atlas timeline {multiverse['ui_label']} "
                    f"after dynamic branch completion."
                ),
            },
        )
        check(report["markdown_artifact_id"], f"{multiverse['ui_label']} markdown report generated")
        check(
            report["content"]["source"]["multiverse_version"] == multiverse["version"],
            f"{multiverse['ui_label']} report is bound to its multiverse version",
        )
        check(
            bool((report["content"].get("ai_summary") or {}).get("executive_summary")),
            f"{multiverse['ui_label']} report-agent summary generated",
        )
        reports.append(report)
    return reports


def validate_final_report(report: dict[str, Any], *, expected_multiverse_count: int) -> None:
    content = report.get("content") or {}
    metadata = report.get("generation_metadata") or {}
    comparison = content.get("multiverse_comparison") or []
    ai_summary = content.get("ai_summary") or {}
    check(report["markdown_artifact_id"], "final Atlas onboarding markdown report generated")
    check(content.get("outcome_distribution"), "final Atlas onboarding structured outcome distribution generated")
    check(
        len(comparison) == expected_multiverse_count,
        "final report compares every terminal multiverse",
    )
    check(
        bool(ai_summary.get("executive_summary")),
        "final report-agent executive summary generated across multiverses",
    )
    check(
        metadata.get("report_agent_status") == "succeeded",
        "final report-agent LLM summary succeeded",
    )


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
        reason = "Atlas onboarding branch: publish live rationing dashboard and invite clinic plus mutual-aid briefings."
        child = create_branch(
            db,
            parent=parent,
            fork_tick_index=tick.tick_index,
            reason=reason,
            idempotency_key=f"atlas-onboarding:manual-transparency:{tick.id}:{uuid.uuid4().hex}",
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
            provenance={"script": "scripts/run_test_big_bang.py", "demo": "atlas_resilience_crisis"},
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

        big_bang = request(
            client,
            base_url,
            "POST",
            "/api/big-bangs",
            expected=201,
            json=sample_payload(
                scenario_text,
                max_tick_index=args.max_tick_index,
                tick_duration_minutes=args.tick_duration_minutes,
                max_active_multiverses=args.max_active_multiverses,
                max_branch_depth=args.max_branch_depth,
                max_branches_per_tick=args.max_branches_per_tick,
                branch_score_threshold=args.branch_score_threshold,
                idle_termination_ticks=args.idle_termination_ticks,
            ),
        )
        big_bang_id = big_bang["id"]
        print(f"[info] Big Bang: {big_bang_id}")
        check(big_bang["status"] == "draft", "Atlas onboarding Big Bang created")

        multiverses = request(client, base_url, "GET", f"/api/big-bangs/{big_bang_id}/multiverses")
        check(len(multiverses) == 1, "root multiverse created")
        root_multiverse_id = multiverses[0]["id"]
        print(f"[info] Root multiverse: {root_multiverse_id}")

        resumed = request(client, base_url, "POST", f"/api/big-bangs/{big_bang_id}/resume")
        check(resumed["status"] == "running", "Atlas onboarding Big Bang resumed")

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
        validate_runtime_or_inheritance(client, base_url, child_tick)
        terminal_multiverses, completion_requests = run_all_multiverses_to_terminal(
            client,
            base_url,
            big_bang_id,
            max_requests=args.completion_max_requests,
        )
        multiverse_reports = generate_multiverse_reports(client, base_url, terminal_multiverses)
        report = request(
            client,
            base_url,
            "POST",
            f"/api/big-bangs/{big_bang_id}/reports/final",
            json={
                "title": "Atlas Resilience Crisis Onboarding Report",
                "summary": (
                    "Generated by scripts/run_test_big_bang.py after full Atlas multiverse "
                    "completion and terminal timeline comparison."
                ),
            },
        )
        validate_final_report(report, expected_multiverse_count=len(terminal_multiverses))
        markdown = request(client, base_url, "GET", f"/api/report-versions/{report['id']}/markdown")
        check("Outcome Distribution" in markdown, "final Atlas onboarding markdown renders outcome distribution")
        render_pdf(client, base_url, report["id"])
        call_count = assert_gemini_only(big_bang_id)

    print("\n== ATLAS ONBOARDING DEMO COMPLETE ==")
    print(f"big_bang_id={big_bang_id}")
    print(f"root_multiverse_id={root_multiverse_id}")
    print(f"child_multiverse_id={child_multiverse_id}")
    print(f"terminal_multiverses={len(terminal_multiverses)}")
    print(f"multiverse_reports={len(multiverse_reports)}")
    print(f"final_report_version_id={report['id']}")
    print(f"completion_requests={completion_requests}")
    print(f"audited_llm_calls={call_count}")
    print(f"model={GEMINI_MODEL}")
    print(f"tick_duration_minutes={args.tick_duration_minutes}")
    print(f"max_tick_index={args.max_tick_index}")
    print(f"derived_horizon_days={round((args.max_tick_index * args.tick_duration_minutes) / 1440, 2)}")
    print(f"view_final_report=worldfork reports view {report['id']}")
    print(f"render_final_pdf=worldfork reports render {report['id']} --format pdf")
    print(f"watch_run=worldfork watch big-bang {big_bang_id}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Atlas onboarding multiverse demo.")
    parser.add_argument("--scenario-file", default=str(DEFAULT_SCENARIO), help="Markdown scenario dossier.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="WorldFork backend base URL.")
    parser.add_argument("--timeout", type=float, default=240.0, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--tick-duration-minutes",
        type=int,
        default=DEFAULT_ATLAS_TICK_DURATION_MINUTES,
        help=(
            "Atlas tick duration. Defaults to 720 minutes (12 hours). "
            "When --max-tick-index is omitted, this determines the derived tick count."
        ),
    )
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=DEFAULT_ATLAS_HORIZON_DAYS,
        help="Target simulated horizon used to derive max ticks when --max-tick-index is omitted.",
    )
    parser.add_argument(
        "--max-tick-index",
        type=int,
        default=None,
        help="Terminal tick index for each Atlas timeline. Defaults to ceil(horizon_days * 1440 / tick_duration_minutes).",
    )
    parser.add_argument(
        "--max-active-multiverses",
        type=int,
        default=64,
        help="High safety cap for simultaneously active Atlas timelines.",
    )
    parser.add_argument(
        "--max-branch-depth",
        type=int,
        default=8,
        help="High safety cap for recursive Atlas branch depth.",
    )
    parser.add_argument(
        "--max-branches-per-tick",
        type=int,
        default=8,
        help="High safety cap for branches admitted from one tick.",
    )
    parser.add_argument(
        "--branch-score-threshold",
        type=float,
        default=0.4,
        help="Lower values admit more God-agent branches during the onboarding demo.",
    )
    parser.add_argument(
        "--idle-termination-ticks",
        type=int,
        default=6,
        help="Idle streak before a low-motion Atlas timeline terminates.",
    )
    parser.add_argument(
        "--completion-max-requests",
        type=int,
        default=1000,
        help="Safety cap for simulate-next-tick requests while draining discovered branches.",
    )
    args = parser.parse_args(argv)
    if args.tick_duration_minutes <= 0:
        parser.error("--tick-duration-minutes must be greater than 0")
    if args.horizon_days <= 0:
        parser.error("--horizon-days must be greater than 0")
    if args.max_tick_index is None:
        args.max_tick_index = max(1, math.ceil(args.horizon_days * 1440 / args.tick_duration_minutes))
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_sample(args)
    except Exception as exc:
        print(f"\nATLAS ONBOARDING DEMO FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
