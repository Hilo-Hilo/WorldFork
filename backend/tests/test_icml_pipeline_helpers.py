from __future__ import annotations

import importlib.util
from pathlib import Path


def load_icml_pipeline():
    script = Path(__file__).resolve().parents[2] / "ICML-forecasting/scripts/icml_pipeline.py"
    spec = importlib.util.spec_from_file_location("icml_pipeline", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_init_job_payload_uses_public_scenario_only(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    case_file = tmp_path / "resolved_001.md"
    case_file.write_text("# Public case\n\nNo private answer here.\n", encoding="utf-8")

    payload = pipeline.build_init_job_payload(
        case_id="resolved_001",
        case_file=case_file,
        name_prefix="E1_init_job",
        max_ticks=1,
        tick_duration_minutes=720,
        branch_policy={
            "max_branch_depth": 1,
            "max_active_multiverses": 1,
            "max_branches_per_tick": 1,
            "branch_score_threshold": 0.999,
        },
    )

    assert payload["job_type"] == "initialize_big_bang"
    assert payload["payload"]["name"] == "E1_init_job_resolved_001"
    assert payload["payload"]["scenario_text"] == "# Public case\n\nNo private answer here.\n"
    assert payload["payload"]["simulation_config"] == {
        "max_ticks": 1,
        "tick_duration_minutes": 720,
    }
    assert "resolution" not in payload["payload"]
    assert "private_eval" not in payload["payload"]["scenario_text"]


def test_resolve_case_file_finds_public_existing_and_additional_cases(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    run_root = tmp_path / "run"
    existing = run_root / "cases/existing_72/civic_001.md"
    additional = run_root / "cases/additional_36/resolved_001.md"
    existing.parent.mkdir(parents=True)
    additional.parent.mkdir(parents=True)
    existing.write_text("existing", encoding="utf-8")
    additional.write_text("additional", encoding="utf-8")

    assert pipeline.resolve_case_file(run_root, "civic_001") == existing
    assert pipeline.resolve_case_file(run_root, "resolved_001") == additional


def test_init_manifest_row_records_evidence_paths() -> None:
    pipeline = load_icml_pipeline()

    row = pipeline.init_manifest_row(
        case_id="resolved_001",
        condition="E1_init_job_codex_only",
        big_bang_id="bb-123",
        job_id="job-123",
        status="completed",
        wait_seconds=12.5,
        run_dir=Path("raw/E1_init_jobs/resolved_001"),
        actor_count=11,
        trait_count=11,
        llm_log_count=1,
    )

    assert row["case_id"] == "resolved_001"
    assert row["big_bang_id"] == "bb-123"
    assert row["job_id"] == "job-123"
    assert row["job_wait_wall_time_seconds"] == 12.5
    assert row["run_dir"] == "raw/E1_init_jobs/resolved_001"
    assert "actors=11" in row["notes"]
    assert "traits=11" in row["notes"]
