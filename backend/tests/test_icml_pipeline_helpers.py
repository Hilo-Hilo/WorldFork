from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


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


def test_public_case_markdown_includes_forecast_clock_and_binary_contract() -> None:
    pipeline = load_icml_pipeline()
    card = {
        "case_id": "resolved_003",
        "benchmark_role": "resolved_forecast",
        "as_of_date": "2025-11-15",
        "forecast_horizon": "through the FOMC decision on 2025-12-10",
        "question": "Will the committee lower rates?",
        "scenario_text": "The committee faces a binary decision.",
        "candidate_endpoints": [
            {"id": "yes", "label": "The event occurs by the deadline"},
            {"id": "no", "label": "The event does not occur by the deadline"},
        ],
    }

    markdown = pipeline.public_case_markdown(card)

    assert "As-of date: 2025-11-15" in markdown
    assert "Forecast horizon: through the FOMC decision on 2025-12-10" in markdown
    assert "Forecast deadline date: 2025-12-10" in markdown
    assert "Binary forecast contract" in markdown
    assert "Auxiliary mechanism endpoints must not keep the binary forecast unresolved" in markdown


def test_resolved_forecast_runtime_context_uses_deadline_aware_tick_duration() -> None:
    pipeline = load_icml_pipeline()

    context = pipeline.resolved_forecast_runtime_context(
        case_id="resolved_003",
        max_ticks=16,
        base_tick_duration_minutes=720,
        deadline_aware=True,
    )

    assert context["tick_duration_minutes"] == 2340
    assert context["forecast_metadata"]["as_of_date"] == "2025-11-15"
    assert context["forecast_metadata"]["forecast_deadline_date"] == "2025-12-10"
    assert "Federal Open Market Committee" in context["question"]
    assert "target range" in context["scenario_text"]
    assert context["source_packet"]
    assert context["endpoint_resolution_keys"] == ["yes", "no"]


def test_build_init_job_payload_preserves_structured_public_forecast_context(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    case_file = tmp_path / "resolved_003.md"
    case_file.write_text("# Public forecast card\n", encoding="utf-8")
    forecast_context = pipeline.resolved_forecast_runtime_context(
        case_id="resolved_003",
        max_ticks=10,
        base_tick_duration_minutes=720,
    )

    payload = pipeline.build_init_job_payload(
        case_id="resolved_003",
        case_file=case_file,
        name_prefix="E3",
        max_ticks=10,
        tick_duration_minutes=forecast_context["tick_duration_minutes"],
        branch_policy={},
        forecast_context=forecast_context,
    )

    scenario_input = payload["payload"]["scenario_input"]
    assert "Federal Open Market Committee" in scenario_input["question"]
    assert "target range" in scenario_input["scenario_text"]
    assert scenario_input["source_packet"]
    assert scenario_input["candidate_endpoints"][0]["id"] == "yes"
    serialized = json.dumps(scenario_input).lower()
    assert "private_eval" not in serialized
    assert "correct_answer" not in serialized


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


def test_assemble_latest_prediction_rows_uses_later_inputs_without_double_counting(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    base = tmp_path / "base.jsonl"
    resume16 = tmp_path / "resume16.jsonl"
    resume32 = tmp_path / "resume32.jsonl"
    output = tmp_path / "assembled.jsonl"
    base.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "resolved_001", "condition": "worldfork_branching_short", "p_yes": 0.2, "route_policy_id": "base"}),
                json.dumps({"case_id": "resolved_003", "condition": "worldfork_branching_short", "p_yes": 0.3, "route_policy_id": "base"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    resume16.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "resolved_001", "condition": "worldfork_branching_short", "p_yes": 0.4, "route_policy_id": "resume16"}),
                json.dumps({"case_id": "resolved_001", "condition": "worldfork_branching_short", "p_yes": 0.5, "route_policy_id": "resume16"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    resume32.write_text(
        json.dumps({"case_id": "resolved_003", "condition": "worldfork_branching_short", "p_yes": 0.8, "route_policy_id": "resume32"}) + "\n",
        encoding="utf-8",
    )

    rows = pipeline.assemble_latest_prediction_rows(
        input_paths=[base, resume16, resume32],
        route_policy_id="assembled_final",
        condition="worldfork_branching_short_final",
    )
    pipeline.assemble_worldfork_latest_predictions(
        SimpleNamespace(
            predictions=[base, resume16, resume32],
            output=output,
            route_policy_id="assembled_final",
            condition="worldfork_branching_short_final",
        )
    )
    written = pipeline.read_jsonl(output)

    assert [(row["case_id"], row["p_yes"]) for row in rows] == [("resolved_001", 0.5), ("resolved_003", 0.8)]
    assert rows == written
    assert all(row["route_policy_id"] == "assembled_final" for row in rows)
    assert rows[0]["source_route_policy_id"] == "resume16"
    assert rows[0]["condition"] == "worldfork_branching_short_final"


def test_extract_worldfork_forecast_uses_candidate_status_masses() -> None:
    pipeline = load_icml_pipeline()

    forecast = pipeline.extract_worldfork_forecast(
        "resolved_001",
        "worldfork_branching_short",
        {
            "endpoint_path_mass_distribution": [
                {
                    "endpoint_key": "yes",
                    "label": "Event occurs",
                    "path_mass": 0.7,
                    "status_path_masses": {"realized": 0.7},
                },
                {
                    "endpoint_key": "no",
                    "label": "Event does not occur",
                    "path_mass": 0.3,
                    "status_path_masses": {"realized": 0.3},
                },
            ]
        },
    )

    assert forecast["p_yes"] == 0.7
    assert forecast["p_no"] == 0.3
    assert forecast["unresolved_mass"] == 0.0
    assert forecast["matched_endpoint_rows"] == 2


def test_extract_worldfork_forecast_splits_unresolved_candidate_mass() -> None:
    pipeline = load_icml_pipeline()

    forecast = pipeline.extract_worldfork_forecast(
        "resolved_001",
        "worldfork_branching_short",
        {
            "endpoint_path_mass_distribution": [
                {
                    "endpoint_key": "yes",
                    "label": "Event occurs",
                    "path_mass": 1.0,
                    "status_path_masses": {"realized": 0.4, "unresolved": 0.2},
                },
                {
                    "endpoint_key": "no",
                    "label": "Event does not occur",
                    "path_mass": 1.0,
                    "status_path_masses": {"realized": 0.2, "unresolved": 0.4},
                },
            ]
        },
    )

    assert forecast["p_yes"] == 0.6
    assert forecast["p_no"] == 0.4
    assert forecast["unresolved_mass"] == 0.4
    assert forecast["yes_realized_mass"] == 0.4
    assert forecast["no_realized_mass"] == 0.2
    assert forecast["matched_endpoint_rows"] == 2


def test_extract_worldfork_forecast_filters_to_explicit_candidate_keys() -> None:
    pipeline = load_icml_pipeline()

    forecast = pipeline.extract_worldfork_forecast(
        "resolved_003",
        "worldfork_branching_short",
        {
            "endpoint_path_mass_distribution": [
                {
                    "endpoint_key": "yes",
                    "label": "The event occurs by the deadline",
                    "path_mass": 0.7,
                    "status_path_masses": {"realized": 0.7},
                },
                {
                    "endpoint_key": "no",
                    "label": "The event does not occur by the deadline",
                    "path_mass": 0.3,
                    "status_path_masses": {"realized": 0.3},
                },
                {
                    "endpoint_key": "market_does_not_price_cut",
                    "label": "Auxiliary mechanism does not price a cut",
                    "path_mass": 1.0,
                    "status_path_masses": {"insufficient_ticks": 1.0},
                },
            ]
        },
        candidate_endpoint_keys=["yes", "no"],
    )

    assert forecast["p_yes"] == 0.7
    assert forecast["p_no"] == 0.3
    assert forecast["unresolved_mass"] == 0.0
    assert forecast["matched_endpoint_rows"] == 2
    assert forecast["extraction_note"] == "derived_from_candidate_endpoint_path_mass_distribution"


def test_compute_direct_prior_blend_scores_reports_grid_and_leave_one_out() -> None:
    pipeline = load_icml_pipeline()

    result = pipeline.compute_direct_prior_blend_scores(
        labels={"case_yes": 1.0, "case_no": 0.0},
        worldfork_predictions={
            "case_yes": {"p_yes": 0.25, "unresolved_mass": 0.2},
            "case_no": {"p_yes": 0.25, "unresolved_mass": 0.4},
        },
        direct_predictions_by_condition={
            "direct_llm": {
                "case_yes": {"p_yes": 0.75, "unresolved_mass": 0.0},
                "case_no": {"p_yes": 0.75, "unresolved_mass": 0.0},
            }
        },
        alphas=[0.0, 0.5, 1.0],
        worldfork_condition="worldfork_branching_short",
    )

    grid = {row["alpha"]: row for row in result["grid_rows"]}
    assert grid[0.5]["n"] == 2
    assert grid[0.5]["mean_brier"] == pytest.approx(0.25)
    assert grid[0.5]["mean_unresolved_mass"] == pytest.approx(0.15)

    selections = {row["selection"]: row for row in result["selection_rows"]}
    assert selections["best_brier_in_sample"]["alpha"] == 0.5
    assert selections["best_brier_in_sample"]["mean_brier"] == pytest.approx(0.25)
    assert selections["leave_one_out_brier_tuned"]["mean_brier"] == pytest.approx(0.5625)
    assert selections["leave_one_out_brier_tuned"]["selected_alpha_mean"] == pytest.approx(0.5)


def test_score_e3_direct_prior_blends_writes_outputs_under_run_root(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    run_root = tmp_path / "run"
    direct_path = run_root / "raw/E2/direct_predictions.jsonl"
    worldfork_path = run_root / "raw/E3/worldfork_predictions.jsonl"
    private_path = tmp_path / "private.jsonl"
    direct_path.parent.mkdir(parents=True)
    worldfork_path.parent.mkdir(parents=True)
    private_path.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "case_yes", "resolution": "yes"}),
                json.dumps({"case_id": "case_no", "resolution": "no"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    direct_path.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "case_yes", "condition": "direct_llm", "p_yes": 0.75}),
                json.dumps({"case_id": "case_no", "condition": "direct_llm", "p_yes": 0.75}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    worldfork_path.write_text(
        "\n".join(
            [
                json.dumps({"case_id": "case_yes", "condition": "worldfork_branching_short", "p_yes": 0.25}),
                json.dumps({"case_id": "case_no", "condition": "worldfork_branching_short", "p_yes": 0.25}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pipeline.PRIVATE_36 = private_path

    pipeline.score_e3_direct_prior_blends(
        SimpleNamespace(
            run_root=run_root,
            worldfork_predictions=Path("raw/E3/worldfork_predictions.jsonl"),
            worldfork_condition="worldfork_branching_short",
            direct_predictions=[Path("raw/E2/direct_predictions.jsonl")],
            alpha_step=0.5,
            grid_output=Path("results/grid.csv"),
            best_output=Path("results/best.csv"),
        )
    )

    assert (run_root / "results/grid.csv").exists()
    assert (run_root / "results/best.csv").exists()
    assert not (tmp_path / "results/grid.csv").exists()


def test_prepare_worldfork_resume_continues_terminal_multiverses(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()

    class FakeClient:
        def __init__(self) -> None:
            self.continued: list[tuple[str, dict]] = []

        def request(self, method: str, path: str, payload: dict | None = None):
            if method == "GET" and path == "/big-bangs/bb-123/multiverses":
                return [
                    {"id": "mv-completed", "status": "completed", "state": {}},
                    {"id": "mv-active-at-horizon", "status": "active", "state": {"runtime_overrides": {"max_ticks": 8}}},
                ]
            if method == "GET" and path == "/multiverses/mv-completed/ticks":
                return [{"tick_index": 5}]
            if method == "GET" and path == "/multiverses/mv-active-at-horizon/ticks":
                return [{"tick_index": 8}]
            if method == "POST" and path.startswith("/multiverses/"):
                assert payload is not None
                self.continued.append((path, payload))
                return {"id": path.rsplit("/", 2)[1], "status": "active"}
            raise AssertionError(f"unexpected request: {method} {path}")

    client = FakeClient()
    run_budget, rows = pipeline._prepare_worldfork_resume(
        client,
        tmp_path,
        big_bang_id="bb-123",
        target_max_ticks=16,
    )

    assert run_budget == 13
    assert [path for path, _payload in client.continued] == [
        "/multiverses/mv-completed/continue",
        "/multiverses/mv-active-at-horizon/continue",
    ]
    assert all(payload["max_ticks"] == 16 for _path, payload in client.continued)
    assert [row["latest_tick_index"] for row in rows] == [5, 8]
    assert (tmp_path / "resume_prepare.json").exists()


def test_resume_job_idempotency_key_stays_inside_db_limit() -> None:
    pipeline = load_icml_pipeline()

    key = pipeline._resume_job_idempotency_key(
        attempt_id="20260505-ledger-resume16",
        route_policy_id="icml_default_deepseek_v4_flash_cohort_hero_branching_core12_resume16",
        condition="worldfork_branching_short",
        case_id="resolved_005",
        big_bang_id="ad8953c8-c989-45cb-9b33-ad8adc87fe1b",
        max_ticks=16,
    )

    assert key.startswith("icml_resume:20260505-ledger-resume16:resolved_005:max16:")
    assert len(key) <= 180


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
        max_ticks_requested=16,
        tick_duration_minutes=720,
        route_policy_id="icml_default_deepseek_v4_flash_cohort_hero",
        prediction_output="raw/E3_worldfork_default_route_16tick/worldfork_predictions.jsonl",
    )

    assert row["case_id"] == "resolved_001"
    assert row["condition"] == "worldfork_no_branch_short"
    assert row["init_job_id"] == "init-job"
    assert row["run_job_id"] == "run-job"
    assert row["ticks_run"] == 3
    assert row["final_report_version_id"] == "rv-123"
    assert row["max_ticks_requested"] == 16
    assert row["route_policy_id"] == "icml_default_deepseek_v4_flash_cohort_hero"
    assert row["prediction_output"] == "raw/E3_worldfork_default_route_16tick/worldfork_predictions.jsonl"


def test_worldfork_long_policy_matches_e4_audit_scope() -> None:
    pipeline = load_icml_pipeline()

    assert pipeline.WORLDFORK_LONG_POLICIES["worldfork_full_branching_long"] == {
        "max_branch_depth": 3,
        "max_active_multiverses": 8,
        "max_branches_per_tick": 2,
        "branch_score_threshold": 0.75,
        "min_branch_runway_ticks": 2,
    }


def test_worldfork_long_manifest_row_records_audit_scope() -> None:
    pipeline = load_icml_pipeline()

    row = pipeline.worldfork_long_manifest_row(
        case_id="civic_002",
        condition="worldfork_full_branching_long",
        big_bang_id="bb-123",
        init_job_id="init-job",
        run_job_id="run-job",
        status="completed",
        init_wait_seconds=120.0,
        run_wait_seconds=240.0,
        run_dir=Path("raw/E4_long_horizon/worldfork_full_branching_long/civic_002"),
        ticks_run=35,
        multiverse_count=8,
        final_report_version_id="rv-123",
        max_ticks_requested=35,
        max_total_ticks_requested=240,
        tick_duration_minutes=720,
        route_policy_id="icml_default_deepseek_v4_flash_cohort_hero_e4_min6",
    )

    assert row["case_id"] == "civic_002"
    assert row["condition"] == "worldfork_full_branching_long"
    assert row["ticks_run"] == 35
    assert row["multiverse_count"] == 8
    assert row["max_total_ticks_requested"] == 240
    assert row["route_policy_id"] == "icml_default_deepseek_v4_flash_cohort_hero_e4_min6"
    assert "audit/social rubrics" in row["notes"]


def test_prediction_key_separates_route_policy_rows() -> None:
    pipeline = load_icml_pipeline()

    row = {
        "case_id": "resolved_001",
        "condition": "worldfork_no_branch_short",
        "route_policy_id": "codex_smoke",
    }

    assert pipeline._prediction_key(row) == (
        "resolved_001",
        "worldfork_no_branch_short",
        "codex_smoke",
    )
    assert pipeline._prediction_key(row, "deepseek_default") == (
        "resolved_001",
        "worldfork_no_branch_short",
        "deepseek_default",
    )


def test_job_finished_treats_interrupt_requested_as_terminal() -> None:
    pipeline = load_icml_pipeline()

    assert pipeline._job_finished({"status": "succeeded"})
    assert pipeline._job_finished({"status": "interrupt_requested"})
    assert not pipeline._job_finished({"status": "running"})


def test_job_wall_seconds_uses_job_timestamps() -> None:
    pipeline = load_icml_pipeline()

    assert (
        pipeline._job_wall_seconds(
            {
                "created_at": "2026-05-05T12:00:00Z",
                "started_at": "2026-05-05T12:00:05Z",
                "finished_at": "2026-05-05T12:01:35Z",
            }
        )
        == 90.0
    )
    assert pipeline._job_wall_seconds({"status": "running"}) == 0.0


def test_manifest_run_job_ids_reads_existing_rows(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    manifest = tmp_path / "worldfork_short_manifest.jsonl"
    pipeline.append_jsonl(manifest, {"run_job_id": "job-1", "status": "completed"})
    pipeline.append_jsonl(manifest, {"run_job_id": "job-2", "status": "failed"})
    pipeline.append_jsonl(manifest, {"status": "missing"})

    assert pipeline._manifest_run_job_ids(manifest) == {"job-1", "job-2"}
    assert pipeline._manifest_run_job_ids(tmp_path / "missing.jsonl") == set()


def test_manifest_run_job_statuses_keep_latest_status(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    manifest = tmp_path / "worldfork_short_manifest.jsonl"
    pipeline.append_jsonl(manifest, {"run_job_id": "job-1", "status": "failed"})
    pipeline.append_jsonl(manifest, {"run_job_id": "job-1", "status": "completed"})
    pipeline.append_jsonl(manifest, {"run_job_id": "job-2", "status": "failed"})
    pipeline.append_jsonl(manifest, {"status": "missing"})

    assert pipeline._manifest_run_job_statuses(manifest) == {
        "job-1": "completed",
        "job-2": "failed",
    }
    assert pipeline._manifest_run_job_statuses(tmp_path / "missing.jsonl") == {}


def test_artifact_wait_seconds_reads_terminal_job_artifact(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    wait_path = tmp_path / "run_job_wait.json"
    wait_path.write_text(
        '{"ok": true, "data": {"started_at": "2026-05-05T12:00:00Z", "finished_at": "2026-05-05T12:00:42Z"}}',
        encoding="utf-8",
    )

    assert pipeline._artifact_wait_seconds(wait_path) == 42.0
    assert pipeline._artifact_wait_seconds(tmp_path / "missing.json") == 0.0


def test_latest_completed_worldfork_short_runs_selects_latest_matching_route(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    manifest = tmp_path / "worldfork_short_manifest.jsonl"
    pipeline.append_jsonl(
        manifest,
        {
            "case_id": "resolved_001",
            "condition": "worldfork_no_branch_short",
            "route_policy_id": "old_route",
            "prediction_output": "raw/old.jsonl",
            "status": "completed",
            "big_bang_id": "old-bb",
            "run_dir": "raw/old",
        },
    )
    pipeline.append_jsonl(
        manifest,
        {
            "case_id": "resolved_001",
            "condition": "worldfork_no_branch_short",
            "route_policy_id": "source_route",
            "prediction_output": "raw/source.jsonl",
            "status": "failed",
            "big_bang_id": "failed-bb",
            "run_dir": "raw/failed",
        },
    )
    pipeline.append_jsonl(
        manifest,
        {
            "case_id": "resolved_001",
            "condition": "worldfork_no_branch_short",
            "route_policy_id": "source_route",
            "prediction_output": "raw/source.jsonl",
            "status": "completed",
            "big_bang_id": "source-bb",
            "run_dir": "raw/source",
        },
    )

    runs = pipeline._latest_completed_worldfork_short_runs(
        manifest,
        source_route_policy_id="source_route",
        source_prediction_output="raw/source.jsonl",
    )

    assert runs[("resolved_001", "worldfork_no_branch_short")]["big_bang_id"] == "source-bb"


def test_worldfork_resume_targets_carry_forward_resolved_and_queue_unresolved(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    manifest = tmp_path / "manifest.jsonl"
    source_predictions = tmp_path / "source.jsonl"
    output_predictions = tmp_path / "resume.jsonl"
    source_route = "source_route"
    target_route = "resume_route"

    for case_id in ("resolved_001", "resolved_002"):
        pipeline.append_jsonl(
            manifest,
            {
                "case_id": case_id,
                "condition": "worldfork_no_branch_short",
                "route_policy_id": source_route,
                "prediction_output": str(source_predictions),
                "status": "completed",
                "big_bang_id": f"{case_id}-bb",
                "run_dir": f"raw/{case_id}",
            },
        )
    pipeline.append_jsonl(
        source_predictions,
        {
            "case_id": "resolved_001",
            "condition": "worldfork_no_branch_short",
            "route_policy_id": source_route,
            "p_yes": 0.2,
            "p_no": 0.8,
            "unresolved_mass": 0.0,
        },
    )
    pipeline.append_jsonl(
        source_predictions,
        {
            "case_id": "resolved_002",
            "condition": "worldfork_no_branch_short",
            "route_policy_id": source_route,
            "p_yes": 0.5,
            "p_no": 0.5,
            "unresolved_mass": 1.0,
        },
    )

    carried, targets = pipeline._worldfork_resume_targets(
        source_predictions=source_predictions,
        output_predictions=output_predictions,
        source_runs=pipeline._latest_completed_worldfork_short_runs(
            manifest,
            source_route_policy_id=source_route,
            source_prediction_output=str(source_predictions),
        ),
        source_route_policy_id=source_route,
        target_route_policy_id=target_route,
        conditions={"worldfork_no_branch_short"},
        case_ids=None,
        skip_resolved_unresolved_mass=0.0,
        max_ticks=35,
        force=False,
    )

    assert len(carried) == 1
    assert carried[0]["case_id"] == "resolved_001"
    assert carried[0]["route_policy_id"] == target_route
    assert carried[0]["resume_status"] == "carried_forward_resolved"
    assert carried[0]["max_ticks_requested"] == 35
    assert [target["case_id"] for target in targets] == ["resolved_002"]
    assert targets[0]["big_bang_id"] == "resolved_002-bb"


def test_resume_additional_ticks_uses_absolute_cap() -> None:
    pipeline = load_icml_pipeline()

    assert pipeline._resume_additional_ticks(latest_tick_index=16, target_max_ticks=35) == 19
    assert pipeline._resume_additional_ticks(latest_tick_index=35, target_max_ticks=35) == 0
    assert pipeline._resume_run_budget(latest_tick_index=16, target_max_ticks=35) == 21
    assert pipeline._resume_run_budget(latest_tick_index=35, target_max_ticks=35) == 2


def test_resume_job_idempotency_key_includes_attempt_id() -> None:
    pipeline = load_icml_pipeline()

    key = pipeline._resume_job_idempotency_key(
        attempt_id="retry2",
        route_policy_id="resume35",
        condition="worldfork_no_branch_short",
        case_id="resolved_001",
        big_bang_id="bb-123",
        max_ticks=35,
    )

    assert key.startswith("icml_resume:retry2:resolved_001:max35:")
    assert len(key) <= 180


def test_generate_e4_paper_artifacts_filters_terminal_runs_and_records_ledger_stop(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    run_root = tmp_path / "run"
    manifest = run_root / "manifests/worldfork_long_horizon_manifest.jsonl"
    terminal_dir = Path("raw/E4_minimum_long_horizon_6/worldfork_full_branching_long/civic_002")
    running_dir = Path("raw/E4_minimum_long_horizon_6/worldfork_full_branching_long/civic_003")

    pipeline.append_jsonl(
        manifest,
        {
            "case_id": "civic_002",
            "condition": "worldfork_full_branching_long",
            "route_policy_id": "icml_default",
            "status": "completed",
            "big_bang_id": "bb-terminal",
            "run_job_id": "run-terminal",
            "run_dir": str(terminal_dir),
            "ticks_run": 12,
            "multiverse_count": 4,
            "max_ticks_requested": 35,
            "max_total_ticks_requested": 240,
        },
    )
    pipeline.append_jsonl(
        manifest,
        {
            "case_id": "civic_003",
            "condition": "worldfork_full_branching_long",
            "status": "running",
            "big_bang_id": "bb-running",
            "run_job_id": "run-running",
            "run_dir": str(running_dir),
        },
    )

    out_dir = run_root / terminal_dir
    out_dir.mkdir(parents=True)
    (out_dir / "run_job_status_latest.json").write_text(
        pipeline.json.dumps(
            {
                "status": "succeeded",
                "result": {
                    "ticks_run": 12,
                    "multiverse_count": 4,
                    "stopped_reason": "completed",
                    "progress": {"completed_ticks": 12, "requested_ticks": 240},
                },
            }
        ),
        encoding="utf-8",
    )
    (out_dir / "path_mass.json").write_text(
        pipeline.json.dumps(
            {
                "ledger_version_id": "ledger-1",
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "policy_adopted",
                        "label": "Policy adopted",
                        "status": "realized",
                        "path_mass": 0.7,
                        "status_path_masses": {"realized": 0.7},
                    },
                    {
                        "endpoint_key": "policy_blocked",
                        "label": "Policy blocked",
                        "status": "eliminated",
                        "path_mass": 0.3,
                        "status_path_masses": {"eliminated": 0.3},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (out_dir / "cost.json").write_text(
        pipeline.json.dumps(
            {
                "data": {
                    "actual": {"openrouter_usd": 1.25},
                    "estimated": {"including_non_openrouter_usd": 1.5},
                    "tokens": {"total_tokens": 5000},
                    "call_count": 9,
                }
            }
        ),
        encoding="utf-8",
    )

    summary = pipeline.generate_e4_paper_artifact_files(
        run_root=run_root,
        input_prefix=Path("raw/E4_minimum_long_horizon_6"),
    )

    assert summary["terminal_runs"] == 1
    assert summary["skipped_nonterminal_runs"] == 1
    audit_rows = list(pipeline.csv.DictReader((run_root / "results/audit_scores.csv").open(encoding="utf-8")))
    assert [row["case_id"] for row in audit_rows] == ["civic_002", "civic_002"]
    assert audit_rows[0]["terminal_state"] == "succeeded"
    assert audit_rows[0]["natural_stop_reason"] == "completed"
    assert audit_rows[0]["natural_stop_ledger_resolved"] == "true"
    assert audit_rows[0]["max_total_ticks_requested"] == "240"
    assert audit_rows[0]["endpoint_key"] == "policy_adopted"

    cost_rows = list(
        pipeline.csv.DictReader((run_root / "results/e4_runtime_cost_summary.csv").open(encoding="utf-8"))
    )
    assert cost_rows[0]["case_id"] == "civic_002"
    assert cost_rows[0]["actual_openrouter_usd"] == "1.250000"
    assert cost_rows[0]["ticks_run"] == "12"


def test_generate_e4_social_state_artifacts_distinguish_failed_and_interrupted(tmp_path: Path) -> None:
    pipeline = load_icml_pipeline()
    run_root = tmp_path / "run"
    manifest = run_root / "manifests/worldfork_long_horizon_manifest.jsonl"

    for status, case_id in [("failed", "health_004"), ("interrupted", "labor_002")]:
        relative_dir = Path(f"raw/E4_minimum_long_horizon_6/worldfork_full_branching_long/{case_id}")
        pipeline.append_jsonl(
            manifest,
            {
                "case_id": case_id,
                "condition": "worldfork_full_branching_long",
                "status": status,
                "big_bang_id": f"bb-{case_id}",
                "run_job_id": f"run-{case_id}",
                "run_dir": str(relative_dir),
                "ticks_run": 3,
            },
        )
        out_dir = run_root / relative_dir
        out_dir.mkdir(parents=True)
        (out_dir / "actors.json").write_text(
            pipeline.json.dumps([{"status": "active"}, {"status": "archived"}]),
            encoding="utf-8",
        )
        (out_dir / "traits.json").write_text(
            pipeline.json.dumps(
                [
                    {"trait_vector": {"behavior_axes": {"assertiveness": 0.8, "caution": 0.4}}},
                    {"trait_vector": {"behavior_axes": {"assertiveness": 0.2}}},
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "graphs.json").write_text(
            pipeline.json.dumps({"edges": [{"weight": 0.5}, {"weight": 0.7}], "nodes": [{"id": "a"}]}),
            encoding="utf-8",
        )
        (out_dir / "sociology_baseline.json").write_text(
            pipeline.json.dumps({"signals": [{"signal": {"level": 0.6}}], "prompt_influences": [{}, {}]}),
            encoding="utf-8",
        )
        (out_dir / "emotion_baseline.json").write_text(
            pipeline.json.dumps({"snapshots": [{}, {}], "observations": [{}]}),
            encoding="utf-8",
        )

    summary = pipeline.generate_e4_paper_artifact_files(
        run_root=run_root,
        input_prefix=Path("raw/E4_minimum_long_horizon_6"),
    )

    assert summary["terminal_runs"] == 2
    social_rows = list(pipeline.csv.DictReader((run_root / "results/social_state_scores.csv").open(encoding="utf-8")))
    assert [row["terminal_state"] for row in social_rows] == ["failed", "interrupted"]
    assert social_rows[0]["actor_count"] == "2"
    assert social_rows[0]["active_actor_count"] == "1"
    assert social_rows[0]["graph_edge_count"] == "2"
    assert social_rows[0]["mean_behavior_assertiveness"] == "0.500000"

    intervals = pipeline.json.loads((run_root / "results/e4_bootstrap_intervals.json").read_text(encoding="utf-8"))
    assert intervals["metrics"]["actor_count"]["n"] == 2
    assert "e4_runtime_cost_summary" in summary["outputs"]
    assert "e4_bootstrap_intervals" in summary["outputs"]
    assert (run_root / "paper/tables/e4_runtime_cost_summary.md").exists()
