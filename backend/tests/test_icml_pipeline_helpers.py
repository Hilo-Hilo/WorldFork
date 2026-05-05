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


def test_init_artifacts_complete_requires_all_capture_files(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    out_dir = tmp_path / "case"
    out_dir.mkdir()

    assert not pipeline.init_artifacts_complete(out_dir)

    for name in pipeline.INIT_ARTIFACT_NAMES:
        (out_dir / f"{name}.json").write_text("{}", encoding="utf-8")

    assert pipeline.init_artifacts_complete(out_dir)


def test_extract_worldfork_forecast_normalizes_yes_no_path_mass() -> None:
    pipeline = load_icml_pipeline()

    forecast = pipeline.extract_worldfork_forecast(
        "resolved_001",
        "worldfork_branching_short",
        {
            "endpoint_path_mass_distribution": [
                {
                    "endpoint_key": "outcome_yes",
                    "label": "Event occurs",
                    "path_mass": 0.7,
                    "status_path_masses": {"realized": 0.7},
                },
                {
                    "endpoint_key": "outcome_no",
                    "label": "Event does not occur",
                    "path_mass": 0.3,
                    "status_path_masses": {"unresolved": 0.3},
                },
            ]
        },
    )

    assert forecast["p_yes"] == 0.7
    assert forecast["p_no"] == 0.3
    assert forecast["unresolved_mass"] == 0.15
    assert forecast["matched_endpoint_rows"] == 2


def test_worldfork_short_manifest_row_records_run_artifacts() -> None:
    pipeline = load_icml_pipeline()

    row = pipeline.worldfork_short_manifest_row(
        case_id="resolved_001",
        condition="worldfork_no_branch_short",
        big_bang_id="bb-123",
        init_job_id="init-job",
        run_job_id="run-job",
        status="completed",
        init_wait_seconds=120.0,
        run_wait_seconds=240.0,
        run_dir=Path("raw/E3_worldfork_short/worldfork_no_branch_short/resolved_001"),
        ticks_run=3,
        multiverse_count=1,
        final_report_version_id="rv-123",
    )

    assert row["case_id"] == "resolved_001"
    assert row["condition"] == "worldfork_no_branch_short"
    assert row["init_job_id"] == "init-job"
    assert row["run_job_id"] == "run-job"
    assert row["ticks_run"] == 3
    assert row["final_report_version_id"] == "rv-123"
