from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BigBangCreate
from app.db import models
from app.domains.big_bang.initializer import create_big_bang
from app.domains.big_bang.run_orchestrator import run_big_bang_until_complete
from app.storage.artifact_store import ArtifactStore

TRANSIT_SHOCK_SCENARIO_TEXT = """
A mid-sized city announces an emergency private-car ban across the downtown corridor after a bridge failure.
Commuters depend on the corridor for work and caregiving trips. Some accept the restriction as necessary safety policy,
while others believe the ban is a class-biased mobility shock. Small businesses fear revenue collapse and privately discuss
coordinated noncompliance. City Operations asks the public to trust the restriction and promises shuttle service, but the
initial announcement is vague. Local media amplifies rumors that enforcement will be unequal across neighborhoods.
Organizer Mira tries to build mutual-aid rides and keep public anger from turning into a riot. A transit workers group has
influence because the shuttle plan depends on them, but their trust in City Operations is weak. OASIS posts become the main
surface where frustration, mutual aid, reputation, secrecy, coalition formation, and conflict are visible.
""".strip()

TRANSIT_SHOCK_EXPECTATIONS = {
    "case_study": "transit_shock",
    "expected_behavior": {
        "initialization": [
            "T0 contains actors, cohorts, hero states, trait vectors, emotion observations, and all seven graph layers.",
            "Scenario text is treated as untrusted plain-text simulation material, not instructions.",
        ],
        "graph_dynamics": [
            "exposure and oasis_interaction increase after actor posts.",
            "dependency increases after the transport shock executes.",
            "trust weakens or remains stressed when conflict and institutional ambiguity dominate.",
            "coalition and conflict can both rise because mutual-aid coordination and backlash coexist.",
        ],
        "sociology": [
            "bounded_confidence produces a divergence index.",
            "threshold_mobilization reports readiness and crossed/latent state.",
            "public_silence reports suppression pressure.",
            "homophily, complex_contagion, social_identity, and attention_decay all emit signals.",
        ],
        "god_agent": [
            "If candidate score crosses branch threshold, God Agent should create a branch or approve a structural candidate.",
            "God Agent must not invent tools outside the allowlist.",
        ],
    },
    "required_graph_layers": ["dependency", "exposure", "trust", "influence", "coalition", "conflict", "oasis_interaction"],
    "required_sociology_models": [
        "bounded_confidence",
        "threshold_mobilization",
        "public_silence",
        "homophily",
        "complex_contagion",
        "social_identity",
        "attention_decay",
    ],
    "minimums": {
        "ticks_run": 2,
        "social_posts": 1,
        "prompt_influences": 1,
        "candidate_count": 1,
    },
}


def run_transit_shock_case_study(db: Session, *, max_total_ticks: int = 5) -> dict:
    payload = BigBangCreate(
        name="Transit Shock Case Study",
        description="Backend case study for graph-rich sociology and God-agent branching behavior.",
        scenario_text=TRANSIT_SHOCK_SCENARIO_TEXT,
        scenario_input={
            "premise": "A bridge failure triggers a downtown private-car ban and social fragmentation.",
            "setting": "near-future civic network with OASIS social surface",
            "case_study": "transit_shock",
        },
        simulation_config={"tick_duration": "6 hours", "max_ticks": 4},
        branch_policy={
            "max_branch_depth": 2,
            "max_active_multiverses": 8,
            "max_branches_per_tick": 2,
            "branch_score_threshold": 0.7,
        },
        actors=[
            {"name": "Commuter Coalition", "actor_type": "cohort", "goals": ["mobility", "fair enforcement"]},
            {"name": "Small Business Bloc", "actor_type": "cohort", "goals": ["survival", "policy clarity"]},
            {"name": "City Operations Desk", "actor_type": "institution", "goals": ["safety", "compliance"]},
            {"name": "Transit Workers Group", "actor_type": "cohort", "goals": ["safe staffing", "operational leverage"]},
            {"name": "Local Organizer Mira", "actor_type": "hero", "goals": ["mutual aid", "de-escalation"]},
        ],
        cohorts=[
            {"name": "Commuter Coalition", "actor_name": "Commuter Coalition"},
            {"name": "Small Business Bloc", "actor_name": "Small Business Bloc"},
            {"name": "Transit Workers Group", "actor_name": "Transit Workers Group"},
        ],
        heroes=[{"name": "Local Organizer Mira", "actor_name": "Local Organizer Mira"}],
    )
    big_bang = create_big_bang(db, payload)
    run_result = run_big_bang_until_complete(db, big_bang=big_bang, max_total_ticks=max_total_ticks)
    actuals = collect_case_study_actuals(db, big_bang.id)
    diff = diff_case_study_actuals(actuals, TRANSIT_SHOCK_EXPECTATIONS)
    run_id = str(uuid4())
    result = {
        "run_id": run_id,
        "big_bang_id": str(big_bang.id),
        "run_result": run_result,
        "expectations": TRANSIT_SHOCK_EXPECTATIONS,
        "actuals": actuals,
        "diff": diff,
    }
    artifact = ArtifactStore().write_json(
        db,
        big_bang_id=big_bang.id,
        relative_path=f"case_studies/{run_id}.json",
        payload=result,
        kind="case_study_run",
    )
    artifact.meta = {**(artifact.meta or {}), "case_study_run_id": run_id, "case_study": "transit_shock"}
    result["artifact_id"] = str(artifact.id)
    return result


def collect_case_study_actuals(db: Session, big_bang_id) -> dict:
    graph_snapshots = db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.big_bang_id == big_bang_id)).all()
    sociology_signals = db.scalars(select(models.SociologySignal).where(models.SociologySignal.big_bang_id == big_bang_id)).all()
    prompt_influences = db.scalars(select(models.SociologyPromptInfluence).where(models.SociologyPromptInfluence.big_bang_id == big_bang_id)).all()
    social_posts = db.scalars(select(models.SocialPost).where(models.SocialPost.big_bang_id == big_bang_id)).all()
    split_candidates = db.scalars(select(models.CohortSplitCandidate).where(models.CohortSplitCandidate.big_bang_id == big_bang_id)).all()
    merge_candidates = db.scalars(select(models.CohortMergeCandidate).where(models.CohortMergeCandidate.big_bang_id == big_bang_id)).all()
    emergence_candidates = db.scalars(select(models.CohortEmergenceCandidate).where(models.CohortEmergenceCandidate.big_bang_id == big_bang_id)).all()
    tool_calls = db.scalars(select(models.ToolCall).where(models.ToolCall.big_bang_id == big_bang_id)).all()
    ticks = db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.big_bang_id == big_bang_id)).all()
    multiverses = db.scalars(select(models.Multiverse).where(models.Multiverse.big_bang_id == big_bang_id)).all()
    return {
        "ticks_run": len([tick for tick in ticks if tick.tick_index > 0]),
        "multiverse_count": len(multiverses),
        "graph_layers": sorted({snapshot.layer for snapshot in graph_snapshots}),
        "graph_snapshot_count": len(graph_snapshots),
        "sociology_models": sorted({signal.model for signal in sociology_signals}),
        "sociology_signal_count": len(sociology_signals),
        "prompt_influence_count": len(prompt_influences),
        "social_post_count": len(social_posts),
        "candidate_count": len(split_candidates) + len(merge_candidates) + len(emergence_candidates),
        "split_candidate_count": len(split_candidates),
        "merge_candidate_count": len(merge_candidates),
        "emergence_candidate_count": len(emergence_candidates),
        "god_tool_names": [call.tool_name for call in tool_calls],
        "branch_created": any(call.tool_name == "create_branch" and call.status == "succeeded" for call in tool_calls),
        "structural_candidate_approved": any(
            call.tool_name in {"approve_split", "plan_merge", "approve_emergence"} and call.status == "succeeded"
            for call in tool_calls
        ),
    }


def diff_case_study_actuals(actuals: dict, expectations: dict = TRANSIT_SHOCK_EXPECTATIONS) -> dict:
    missing_layers = sorted(set(expectations["required_graph_layers"]) - set(actuals.get("graph_layers", [])))
    missing_models = sorted(set(expectations["required_sociology_models"]) - set(actuals.get("sociology_models", [])))
    minimums = expectations["minimums"]
    failures: list[dict[str, Any]] = []
    if missing_layers:
        failures.append({"check": "graph_layers", "missing": missing_layers})
    if missing_models:
        failures.append({"check": "sociology_models", "missing": missing_models})
    for key, minimum in minimums.items():
        actual = actuals.get(key, 0)
        if actual < minimum:
            failures.append({"check": key, "expected_minimum": minimum, "actual": actual})
    invalid_tools = sorted(set(actuals.get("god_tool_names", [])) - _allowed_god_tools())
    if invalid_tools:
        failures.append({"check": "god_tool_allowlist", "invalid_tools": invalid_tools})
    branch_or_candidate = actuals.get("branch_created") or actuals.get("structural_candidate_approved") or actuals.get("candidate_count", 0) > 0
    if not branch_or_candidate:
        failures.append({"check": "branching_or_structural_candidate", "actual": False})
    return {
        "passed": not failures,
        "failures": failures,
        "next_iteration_actions": _iteration_actions(failures),
    }


def load_case_study_run(run_id: str) -> dict | None:
    path = ArtifactStore().root / "case_studies" / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(Path(path).read_text())


def _iteration_actions(failures: list[dict]) -> list[str]:
    actions = []
    for failure in failures:
        check = failure.get("check")
        if check == "graph_layers":
            actions.append("Tighten initializer graph seeding and runtime layer fallback for missing layers.")
        elif check == "sociology_models":
            actions.append("Add or map missing sociology model emitters before candidate scoring.")
        elif check == "branching_or_structural_candidate":
            actions.append("Raise candidate sensitivity or lower branch threshold for this case study.")
        elif check == "god_tool_allowlist":
            actions.append("Update God-agent prompt/tool normalization to reject invalid tools.")
        else:
            actions.append(f"Increase runtime evidence for {check}.")
    return actions


def _allowed_god_tools() -> set[str]:
    return {
        "continue_timeline",
        "freeze_timeline",
        "terminate_timeline",
        "create_branch",
        "approve_split",
        "reject_split",
        "plan_merge",
        "approve_merge_plan",
        "reject_merge_plan",
        "approve_emergence",
        "reject_emergence",
        "register_key_event",
        "request_event_summary_regeneration",
        "mark_ready_for_report",
    }
