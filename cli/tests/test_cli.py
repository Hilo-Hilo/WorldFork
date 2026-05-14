import sys
import types

import pytest
from click.testing import CliRunner

import worldfork_cli.main as cli_main
from worldfork_cli.main import main


def test_help_lists_agent_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "agent" in result.output
    assert "runs" in result.output
    assert "jobs" in result.output
    assert "settings" in result.output
    assert "update" in result.output
    assert "setup" in result.output
    assert "demo" in result.output
    assert "smoke" in result.output
    assert "demo atlas" in result.output


def _make_source_checkout(path) -> None:
    (path / ".git").mkdir()
    (path / "backend" / "app").mkdir(parents=True)
    (path / "cli" / "src" / "worldfork_cli").mkdir(parents=True)


def test_update_dry_run_fetches_without_merging(monkeypatch, tmp_path) -> None:
    _make_source_checkout(tmp_path)
    calls = []

    def fake_run_git(repo, args):
        calls.append(args)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return types.SimpleNamespace(stdout="dev\n")
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return types.SimpleNamespace(stdout="")
        if args == ["rev-parse", "HEAD"]:
            return types.SimpleNamespace(stdout="abc123\n")
        if args == ["fetch", "--prune", "origin", "+refs/heads/dev:refs/remotes/origin/dev"]:
            return types.SimpleNamespace(stdout="")
        if args[:2] == ["diff", "--name-only"]:
            return types.SimpleNamespace(stdout="")
        if args[:3] == ["rev-list", "--left-right", "--count"]:
            return types.SimpleNamespace(stdout="0\t2\n")
        raise AssertionError(args)

    monkeypatch.setattr(cli_main, "_run_git", fake_run_git)

    result = CliRunner().invoke(main, ["--json", "update", "--repo", str(tmp_path), "--dry-run"])

    assert result.exit_code == 0
    assert '"status": "would_update"' in result.output
    assert ["merge", "--ff-only", "refs/remotes/origin/dev"] not in calls


def test_update_merges_fast_forward_and_can_reinstall_cli(monkeypatch, tmp_path) -> None:
    _make_source_checkout(tmp_path)
    git_calls = []
    command_calls = []
    rev_parse_heads = iter(["abc123\n", "def456\n"])

    def fake_run_git(repo, args):
        git_calls.append(args)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return types.SimpleNamespace(stdout="dev\n")
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return types.SimpleNamespace(stdout="")
        if args == ["rev-parse", "HEAD"]:
            return types.SimpleNamespace(stdout=next(rev_parse_heads))
        if args == ["fetch", "--prune", "origin", "+refs/heads/dev:refs/remotes/origin/dev"]:
            return types.SimpleNamespace(stdout="")
        if args[:2] == ["diff", "--name-only"]:
            return types.SimpleNamespace(stdout="")
        if args[:3] == ["rev-list", "--left-right", "--count"]:
            return types.SimpleNamespace(stdout="0\t1\n")
        if args == ["merge", "--ff-only", "refs/remotes/origin/dev"]:
            return types.SimpleNamespace(stdout="")
        raise AssertionError(args)

    def fake_run_command(repo, command):
        command_calls.append(command)
        return types.SimpleNamespace(stdout="")

    monkeypatch.setattr(cli_main, "_run_git", fake_run_git)
    monkeypatch.setattr(cli_main, "_run_command", fake_run_command)

    result = CliRunner().invoke(
        main,
        ["--json", "update", "--repo", str(tmp_path), "--yes", "--install-cli"],
    )

    assert result.exit_code == 0
    assert '"status": "updated"' in result.output
    assert ["merge", "--ff-only", "refs/remotes/origin/dev"] in git_calls
    assert command_calls == [[sys.executable, "-m", "pip", "install", "-e", "./cli"]]


def test_update_refuses_dirty_tracked_files(monkeypatch, tmp_path) -> None:
    _make_source_checkout(tmp_path)
    calls = []

    def fake_run_git(repo, args):
        calls.append(args)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return types.SimpleNamespace(stdout="dev\n")
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return types.SimpleNamespace(stdout=" M cli/src/worldfork_cli/main.py\n")
        raise AssertionError(args)

    monkeypatch.setattr(cli_main, "_run_git", fake_run_git)

    result = CliRunner().invoke(main, ["update", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "dirty tracked files" in result.output
    assert ["fetch", "--prune", "origin", "+refs/heads/dev:refs/remotes/origin/dev"] not in calls


def test_update_refuses_remote_changes_to_protected_paths(monkeypatch, tmp_path) -> None:
    _make_source_checkout(tmp_path)

    def fake_run_git(repo, args):
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return types.SimpleNamespace(stdout="dev\n")
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return types.SimpleNamespace(stdout="")
        if args == ["rev-parse", "HEAD"]:
            return types.SimpleNamespace(stdout="abc123\n")
        if args == ["fetch", "--prune", "origin", "+refs/heads/dev:refs/remotes/origin/dev"]:
            return types.SimpleNamespace(stdout="")
        if args[:2] == ["diff", "--name-only"]:
            return types.SimpleNamespace(stdout=".env\n")
        raise AssertionError(args)

    monkeypatch.setattr(cli_main, "_run_git", fake_run_git)

    result = CliRunner().invoke(main, ["update", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "protected local config/data paths" in result.output
    assert ".env" in result.output


def test_update_refuses_remote_changes_to_reports(monkeypatch, tmp_path) -> None:
    _make_source_checkout(tmp_path)

    def fake_run_git(repo, args):
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return types.SimpleNamespace(stdout="main\n")
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return types.SimpleNamespace(stdout="")
        if args == ["rev-parse", "HEAD"]:
            return types.SimpleNamespace(stdout="abc123\n")
        if args == ["fetch", "--prune", "origin", "+refs/heads/main:refs/remotes/origin/main"]:
            return types.SimpleNamespace(stdout="")
        if args[:2] == ["diff", "--name-only"]:
            assert "reports" in args
            return types.SimpleNamespace(stdout="reports/final.pdf\n")
        raise AssertionError(args)

    monkeypatch.setattr(cli_main, "_run_git", fake_run_git)

    result = CliRunner().invoke(main, ["update", "--repo", str(tmp_path)])

    assert result.exit_code == 1
    assert "protected local config/data paths" in result.output
    assert "reports/final.pdf" in result.output


def test_global_verbosity_parses_before_command() -> None:
    result = CliRunner().invoke(main, ["--verbosity", "normal", "agent", "--help"])

    assert result.exit_code == 0
    assert "discover" in result.output


def test_subcommand_timeout_is_not_stolen_by_global_parser(monkeypatch) -> None:
    client_timeouts: list[float] = []
    watch_args: list[tuple[float, float]] = []

    class FakeClient:
        def __init__(self, _base_url, _api_prefix, timeout) -> None:
            client_timeouts.append(timeout)

    def fake_watch(ctx, big_bang_id, poll_interval, timeout_seconds, limit, once, json_lines, stop):
        watch_args.append((poll_interval, timeout_seconds))

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)
    monkeypatch.setattr(cli_main, "_watch_big_bang", fake_watch)

    result = CliRunner().invoke(main, ["watch", "big-bang", "bb-123", "--timeout", "7", "--once"])

    assert result.exit_code == 0
    assert client_timeouts == [30]
    assert watch_args == [(1, 7)]


@pytest.mark.parametrize(
    ("args", "expected_method", "expected_path", "expected_params"),
    [
        (["runs", "workspace", " bb-123 "], "GET", "/agent/runs/bb-123/workspace", {"verbosity": "summary"}),
        (["runs", "timing", " bb-123 "], "GET", "/agent/runs/bb-123/timing", {"verbosity": "summary"}),
        (["runs", "cost", " bb-123 "], "GET", "/agent/runs/bb-123/cost", {"verbosity": "summary"}),
        (["runs", "estimate", " bb-123 "], "POST", "/agent/runs/bb-123/cost-estimate", None),
        (["runs", "delete", " bb-123 "], "DELETE", "/big-bangs/bb-123", None),
        (
            ["multiverses", "trace", " mv-123 ", "--tick", "3"],
            "GET",
            "/agent/universes/mv-123/trace",
            {"verbosity": "summary", "tick": 3},
        ),
        (
            ["cohorts", "transcript", " cohort-123 ", "--multiverse-id", " mv-123 "],
            "GET",
            "/agent/cohorts/cohort-123/transcript",
            {"verbosity": "summary", "multiverse_id": "mv-123", "from_tick": 0, "to_tick": 10},
        ),
        (["ticks", "timing", " tick-123 "], "GET", "/ticks/tick-123/timing", {"verbosity": "summary"}),
        (["ticks", "cost", " tick-123 "], "GET", "/ticks/tick-123/cost", {"verbosity": "summary"}),
        (["jobs", "wait", " job-123 ", "--timeout", "0"], "POST", "/agent/jobs/job-123/wait", None),
    ],
)
def test_cli_path_ids_trim_surrounding_whitespace(
    monkeypatch,
    args,
    expected_method,
    expected_path,
    expected_params,
) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            if path.endswith("/wait"):
                return {
                    "ok": True,
                    "data": {"id": "job-123", "status": "completed"},
                    "meta": {"terminal": True, "timed_out": False},
                }
            return {"ok": True, "data": {}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0
    method, path, params, _json_body = calls[0]
    assert (method, path) == (expected_method, expected_path)
    if expected_params is not None:
        assert params == expected_params


@pytest.mark.parametrize(
    ("args", "expected_method", "expected_path", "expected_params"),
    [
        (["reports", "list", " bb-123 "], "GET", "/big-bangs/bb-123/reports", None),
        (["reports", "versions", " report-123 "], "GET", "/reports/report-123/versions", None),
        (["reports", "generate", "multiverse", " mv-123 "], "POST", "/multiverses/mv-123/report", None),
        (["reports", "generate", "final", " bb-123 "], "POST", "/big-bangs/bb-123/reports/final", None),
        (["reports", "pack", " bb-123 "], "GET", "/big-bangs/bb-123/report-evidence-pack", {"mode": "standard"}),
        (
            ["reports", "adjudicate", " bb-123 "],
            "POST",
            "/big-bangs/bb-123/timeline-adjudications/evaluate",
            None,
        ),
        (
            ["reports", "adjudication", " bb-123 "],
            "GET",
            "/big-bangs/bb-123/timeline-adjudications/latest",
            None,
        ),
        (["reports", "view", " rv-123 ", "--format", "json"], "GET", "/report-versions/rv-123", None),
        (["watch", "big-bang", " bb-123 ", "--once"], "GET", "/workspace/bb-123/state", None),
        (["watch", "multiverse", " m-1 ", "--once"], "GET", "/multiverses/m-1", None),
    ],
)
def test_cli_report_and_watch_ids_trim_surrounding_whitespace(
    monkeypatch,
    args,
    expected_method,
    expected_path,
    expected_params,
) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            if path == "/workspace/bb-123/state":
                return {"big_bang": {"id": "bb-123", "status": "archived"}, "multiverses": [], "latest_ticks": []}
            if path == "/workspace/bb-123/activity":
                return {"ticks": [], "tool_calls": []}
            if path == "/multiverses/m-1":
                return {"id": "m-1", "big_bang_id": "bb-123", "status": "merged"}
            if path == "/multiverses/m-1/ticks":
                return []
            if path == "/agent/logs":
                return {"ok": True, "data": [], "meta": {}}
            return {"ok": True, "data": {}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0
    assert (expected_method, expected_path) in [(method, path) for method, path, _params, _body in calls]
    if expected_params is not None:
        matching_params = [params for method, path, params, _body in calls if (method, path) == (expected_method, expected_path)]
        assert matching_params == [expected_params]
    assert all(" " not in path for _method, path, _params, _body in calls)


def test_cli_rejects_invalid_timeouts_before_requests() -> None:
    cases = [
        ["--timeout", "0", "status"],
        ["init", "--name", "Run", "--wait-timeout", "0"],
        ["jobs", "wait", "job-123", "--timeout", "-1"],
        ["watch", "big-bang", "bb-123", "--timeout", "-1"],
        ["watch", "multiverse", "m-1", "--timeout", "-1"],
        ["ledgers", "evaluate", "bb-123", "--wait", "--timeout", "-1"],
        ["settings", "openai-codex-login", "--timeout", "0"],
        ["demo", "atlas", "--timeout", "0"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_cli_rejects_invalid_init_simulation_overrides_before_requests(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, *_args, **_kwargs):
            raise AssertionError("backend request should not run")

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    cases = [
        ["init", "--name", "Run", "--max-ticks", "0"],
        ["init", "--name", "Run", "--tick-duration-minutes", "0"],
        ["init", "--name", "Run", "--max-schedule-horizon-ticks", "0"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_cli_rejects_negative_tick_selectors_before_requests(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, *_args, **_kwargs):
            raise AssertionError("backend request should not run")

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    cases = [
        ["multiverses", "trace", "mv-123", "--tick", "-1"],
        ["cohorts", "transcript", "cohort-1", "--multiverse-id", "mv-123", "--from-tick", "-1"],
        ["cohorts", "transcript", "cohort-1", "--multiverse-id", "mv-123", "--to-tick", "-1"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_cli_rejects_invalid_pagination_before_requests(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, *_args, **_kwargs):
            raise AssertionError("backend request should not run")

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    cases = [
        ["runs", "list", "--limit", "0"],
        ["runs", "list", "--limit", "201"],
        ["runs", "list", "--offset", "-1"],
        ["jobs", "list", "--limit", "0"],
        ["jobs", "list", "--limit", "501"],
        ["jobs", "list", "--offset", "-1"],
        ["logs", "list", "--limit", "0"],
        ["logs", "list", "--limit", "501"],
        ["logs", "list", "--offset", "-1"],
        ["watch", "big-bang", "bb-123", "--limit", "0"],
        ["watch", "multiverse", "m-1", "--limit", "0"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_cli_rejects_invalid_cost_estimate_options_before_requests() -> None:
    cases = [
        ["runs", "estimate", "bb-123", "--remaining-ticks", "-1"],
        ["runs", "estimate", "bb-123", "--max-ticks", "-1"],
        ["runs", "estimate", "bb-123", "--branch-threshold", "-0.1"],
        ["runs", "estimate", "bb-123", "--branch-threshold", "1.1"],
        ["runs", "estimate", "bb-123", "--max-parallel-cohort-decisions", "0"],
        ["costs", "estimate", "--remaining-ticks", "-1"],
        ["costs", "estimate", "--max-ticks", "-1"],
        ["costs", "estimate", "--branch-threshold", "-0.1"],
        ["costs", "estimate", "--branch-threshold", "1.1"],
        ["costs", "estimate", "--max-parallel-cohort-decisions", "0"],
        ["costs", "estimate", "--assumed-cohorts", "-1"],
        ["costs", "estimate", "--assumed-heroes", "-1"],
        ["costs", "estimate", "--assumed-multiverses", "0"],
        ["costs", "estimate", "--scenario-tokens", "-1"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_cli_rejects_invalid_atlas_demo_options_before_harness(monkeypatch) -> None:
    def fail_harness(*_args, **_kwargs) -> None:
        raise AssertionError("harness should not run")

    monkeypatch.setattr(cli_main, "_run_source_harness", fail_harness)

    cases = [
        ["demo", "atlas", "--tick-duration-minutes", "0"],
        ["demo", "atlas", "--horizon-days", "0"],
        ["demo", "atlas", "--max-tick-index", "-1"],
        ["demo", "atlas", "--max-active-multiverses", "0"],
        ["demo", "atlas", "--max-branch-depth", "0"],
        ["demo", "atlas", "--max-branches-per-tick", "0"],
        ["demo", "atlas", "--branch-score-threshold", "-0.1"],
        ["demo", "atlas", "--branch-score-threshold", "1.1"],
        ["demo", "atlas", "--idle-termination-ticks", "-1"],
        ["demo", "atlas", "--completion-max-requests", "0"],
    ]

    for args in cases:
        result = CliRunner().invoke(main, args)

        assert result.exit_code == 2, args
        assert "Invalid value for" in result.output


def test_jobs_pause_calls_canonical_job_control_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"id": "job-123", "status": "paused"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["jobs", "pause", "job-123"])

    assert result.exit_code == 0
    assert calls == [("POST", "/jobs/job-123/pause", None, None)]


def test_runs_timing_calls_agent_timing_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"ok": True, "data": {"big_bang_id": "bb-123", "ticks": []}, "meta": {}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["runs", "timing", "bb-123"])

    assert result.exit_code == 0
    assert calls == [("GET", "/agent/runs/bb-123/timing", {"verbosity": "summary"}, None)]
    assert "big_bang_id" in result.output


def test_ticks_timing_calls_tick_timing_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"tick_snapshot_id": "tick-123", "duration_seconds": 12.5}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ticks", "timing", "tick-123"])

    assert result.exit_code == 0
    assert calls == [("GET", "/ticks/tick-123/timing", {"verbosity": "summary"}, None)]
    assert "duration_seconds" in result.output


def test_runs_cost_calls_agent_cost_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"currency": "USD", "actual": {"openrouter_usd": 0.01}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["runs", "cost", "bb-123"])

    assert result.exit_code == 0
    assert calls == [("GET", "/agent/runs/bb-123/cost", {"verbosity": "summary"}, None)]
    assert "openrouter_usd" in result.output


def test_ticks_cost_calls_tick_cost_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"currency": "USD", "tokens": {"total_tokens": 100}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ticks", "cost", "tick-123"])

    assert result.exit_code == 0
    assert calls == [("GET", "/ticks/tick-123/cost", {"verbosity": "summary"}, None)]
    assert "total_tokens" in result.output


def test_runs_estimate_posts_cost_and_time_estimate_payload(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"currency": "USD", "time_estimate": {"estimated_wall_seconds": 12}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        [
            "runs",
            "estimate",
            "bb-123",
            "--remaining-ticks",
            "5",
            "--max-parallel-cohort-decisions",
            "8",
            "--model-config",
            '{"cohort_agent_model":"deepseek/deepseek-v4-flash"}',
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/agent/runs/bb-123/cost-estimate",
            None,
            {
                "remaining_ticks": 5,
                "max_ticks": None,
                "branch_threshold": None,
                "max_parallel_cohort_decisions": 8,
                "model_config": {"cohort_agent_model": "deepseek/deepseek-v4-flash"},
                "simulation_config": {},
                "include_agent_types": [],
                "exclude_agent_types": [],
                "include_non_openrouter": True,
                "include_reports": True,
            },
        )
    ]
    assert "estimated_wall_seconds" in result.output


def test_costs_estimate_posts_pre_big_bang_payload(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params, json_body))
            return {"scope": "pre_big_bang", "currency": "USD"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        [
            "costs",
            "estimate",
            "--assumed-cohorts",
            "4",
            "--assumed-heroes",
            "1",
            "--scenario-tokens",
            "128000",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/costs/estimate",
            None,
            {
                "remaining_ticks": None,
                "max_ticks": None,
                "branch_threshold": None,
                "max_parallel_cohort_decisions": None,
                "assumed_cohorts": 4,
                "assumed_heroes": 1,
                "assumed_multiverses": None,
                "scenario_tokens": 128000,
                "model_config": {},
                "simulation_config": {},
                "include_agent_types": [],
                "exclude_agent_types": [],
                "include_non_openrouter": True,
                "include_reports": True,
            },
        )
    ]
    assert "pre_big_bang" in result.output


def test_reports_render_calls_report_version_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def response(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return types.SimpleNamespace(
                content=b"%PDF-1.4\n",
                headers={"content-type": "application/pdf", "x-worldfork-render-mode": "ephemeral"},
                encoding=None,
            )

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["reports", "render", "rv-123", "--format", "pdf"])

    assert result.exit_code == 0
    assert '"persisted": false' in result.output
    assert '"artifact_id": null' in result.output
    assert calls == [
        (
            "POST",
            "/report-versions/rv-123/render",
            None,
            {"format": "pdf"},
        )
    ]


def test_reports_pack_and_adjudication_commands_call_canonical_endpoints(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"ok": True}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    runner = CliRunner()
    pack = runner.invoke(main, ["reports", "pack", "bb-123", "--mode", "summary"])
    adjudicate = runner.invoke(main, ["reports", "adjudicate", "bb-123", "--summary", "posthoc"])
    adjudication = runner.invoke(main, ["reports", "adjudication", "bb-123"])

    assert pack.exit_code == 0
    assert adjudicate.exit_code == 0
    assert adjudication.exit_code == 0
    assert calls == [
        ("GET", "/big-bangs/bb-123/report-evidence-pack", {"mode": "summary"}, None),
        (
            "POST",
            "/big-bangs/bb-123/timeline-adjudications/evaluate",
            None,
            {"source_type": "posthoc_cli", "summary": "posthoc"},
        ),
        ("GET", "/big-bangs/bb-123/timeline-adjudications/latest", None, None),
    ]


def test_reports_generate_commands_call_backend_report_endpoints(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"ok": True}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    runner = CliRunner()
    multiverse = runner.invoke(main, ["reports", "generate", "multiverse", "mv-123", "--title", "M report"])
    final = runner.invoke(main, ["reports", "generate", "final", "bb-123", "--summary", "done"])

    assert multiverse.exit_code == 0
    assert final.exit_code == 0
    assert calls == [
        ("POST", "/multiverses/mv-123/report", None, {"title": "M report", "summary": None}),
        ("POST", "/big-bangs/bb-123/reports/final", None, {"title": None, "summary": "done"}),
    ]


def test_ledgers_evaluate_calls_endpoint_ledger_job(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"job_id": "job-123", "status": "queued"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ledgers", "evaluate", "bb-123", "--multiverse-id", "mv-123"])

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/multiverses/mv-123/endpoint-ledgers/evaluate",
            None,
            {"idempotency_key": None, "run_inline": False, "candidate_endpoint": None},
        )
    ]


def test_multiverses_trace_calls_agent_trace_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"trace": []}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        ["multiverses", "trace", "mv-123", "--tick", "3", "--actor-kind", "cohort"],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "GET",
            "/agent/universes/mv-123/trace",
            {"verbosity": "summary", "tick": 3, "actor_kind": "cohort"},
            None,
        )
    ]


def test_ledgers_path_mass_calls_plot_data_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"endpoint_path_mass_distribution": []}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ledgers", "path-mass", "bb-123"])

    assert result.exit_code == 0
    assert calls == [("GET", "/big-bangs/bb-123/endpoint-ledgers/path-mass", None, None)]


def test_ledgers_evaluate_wait_exits_nonzero_for_unsuccessful_terminal(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            if path.endswith("/endpoint-ledgers/evaluate"):
                return {"job_id": "job-123", "status": "queued"}
            return {
                "ok": True,
                "data": {"id": "job-123", "status": "cancelled", "result": {}},
                "meta": {"terminal": True, "timed_out": False},
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ledgers", "evaluate", "bb-123", "--wait", "--timeout", "0"])

    assert result.exit_code == 2
    assert calls[0][0:2] == ("POST", "/big-bangs/bb-123/endpoint-ledgers/evaluate")
    assert calls[1][0:2] == ("POST", "/agent/jobs/job-123/wait")


def test_models_defaults_calls_agent_models(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"default_model": "deepseek/deepseek-v4-flash"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["models", "defaults"])

    assert result.exit_code == 0
    assert calls == [("GET", "/agent/models", None, None)]


def test_runs_delete_calls_canonical_big_bang_delete(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"id": "bb-123", "status": "archived"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["runs", "delete", "bb-123"])

    assert result.exit_code == 0
    assert calls == [("DELETE", "/big-bangs/bb-123", None, None)]


def test_settings_patch_calls_settings_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"default_tick_duration_minutes": 90, "payload": {"demo": True}}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        ["settings", "patch", "--data", '{"default_tick_duration_minutes":90,"payload":{"demo":true}}'],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "PATCH",
            "/settings",
            None,
            {"default_tick_duration_minutes": 90, "payload": {"demo": True}},
        )
    ]


def test_settings_llm_calls_llm_config_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"known_routes": [{"route": "report_agent"}]}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["settings", "llm"])

    assert result.exit_code == 0
    assert calls == [("GET", "/settings/llm", None, None)]
    assert "report_agent" in result.output


def test_setup_reads_llm_config_and_emits_provider_options(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {
                "provider_catalog": [
                    {
                        "provider": "openrouter",
                        "enabled": True,
                        "configured": True,
                        "default_model": "deepseek/deepseek-v4-flash",
                        "source": "runtime_defaults",
                    }
                ],
                "effective_model_routing": [{"route": "cohort_agent"}],
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0
    assert calls == [("GET", "/settings/llm", None, None)]
    assert "atlas-fast-governed" in result.output
    assert "OPENROUTER_API_KEY" in result.output
    assert "openai-codex" in result.output
    assert "gpt-5.4" in result.output
    assert "default strong-provider lane" in result.output
    assert "deepseek/deepseek-v4-pro" in result.output
    assert "OpenRouter-hosted DeepSeek Pro/Claude/OpenAI-compatible substitutes" in result.output
    assert "model_routing_patch" not in result.output


def test_setup_include_patch_emits_atlas_routing_payload(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            return {"provider_catalog": [], "effective_model_routing": []}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["setup", "--include-patch"])

    assert result.exit_code == 0
    assert "model_routing_patch" in result.output
    assert "cohort_agent" in result.output
    assert "report_agent" in result.output
    assert "governance_substitutes" in result.output
    assert "actor_deliberation_call" not in result.output


def test_setup_offline_does_not_contact_backend(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            raise AssertionError("backend should not be contacted")

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["setup", "--offline"])

    assert result.exit_code == 0
    assert '"backend_reachable": false' in result.output
    assert "offline mode" in result.output
    assert "ollama" in result.output
    assert "vllm" in result.output
    assert "lmstudio" in result.output


def test_setup_keeps_working_when_backend_unreachable(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            raise cli_main.CliError("request failed for api/settings/llm")

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["setup"])

    assert result.exit_code == 0
    assert '"backend_reachable": false' in result.output
    assert "request failed for api/settings/llm" in result.output


def test_init_accepts_long_inline_json_without_path_probe(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, timeout=None):
            calls.append((method, path, params, json_body, timeout))
            if method == "POST":
                return {"id": "bb-123", "name": "Runtime check"}
            return {"ok": True, "path": path}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)
    actors = (
        '[{"name":"Transit Riders","actor_type":"cohort","description":"'
        + ("residents affected by fare changes " * 20)
        + '"}]'
    )

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--name",
            "Runtime check",
            "--scenario",
            "Transit fare increase",
            "--no-initializer-agent",
            "--actors",
            actors,
        ],
    )

    assert result.exit_code == 0
    post = calls[0]
    assert post[0:2] == ("POST", "/big-bangs")
    assert post[3]["actors"][0]["name"] == "Transit Riders"


def test_demo_atlas_invokes_source_harness(monkeypatch, tmp_path) -> None:
    calls = []
    scenario_file = tmp_path / "atlas.md"
    scenario_file.write_text("Atlas scenario", encoding="utf-8")

    scripts_pkg = types.ModuleType("scripts")
    harness = types.ModuleType("scripts.run_test_big_bang")

    def fake_main(argv):
        calls.append(argv)
        return 0

    harness.main = fake_main
    monkeypatch.setitem(sys.modules, "scripts", scripts_pkg)
    monkeypatch.setitem(sys.modules, "scripts.run_test_big_bang", harness)

    result = CliRunner().invoke(
        main,
        [
            "--base-url",
            "http://worldfork.test",
            "--api-prefix",
            "/custom-api",
            "demo",
            "atlas",
            "--scenario-file",
            str(scenario_file),
            "--horizon-days",
            "2",
            "--max-tick-index",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert calls
    assert calls[0][:4] == ["--base-url", "http://worldfork.test", "--api-prefix", "custom-api"]
    assert "--expected-provider" not in calls[0]
    assert "--expected-model" not in calls[0]
    assert ["--scenario-file", str(scenario_file.resolve())] == calls[0][-4:-2]
    assert calls[0][-2:] == ["--max-tick-index", "4"]


def test_demo_atlas_passes_explicit_expected_pair(monkeypatch, tmp_path) -> None:
    calls = []
    scenario_file = tmp_path / "atlas.md"
    scenario_file.write_text("Atlas scenario", encoding="utf-8")

    scripts_pkg = types.ModuleType("scripts")
    harness = types.ModuleType("scripts.run_test_big_bang")
    harness.main = lambda argv: calls.append(argv) or 0
    monkeypatch.setitem(sys.modules, "scripts", scripts_pkg)
    monkeypatch.setitem(sys.modules, "scripts.run_test_big_bang", harness)

    result = CliRunner().invoke(
        main,
        [
            "demo",
            "atlas",
            "--scenario-file",
            str(scenario_file),
            "--expected-provider",
            "openrouter-claude",
            "--expected-model",
            "anthropic/claude-sonnet-4.5",
        ],
    )

    assert result.exit_code == 0
    assert "--expected-provider" in calls[0]
    assert calls[0][calls[0].index("--expected-provider") + 1] == "openrouter-claude"
    assert calls[0][calls[0].index("--expected-model") + 1] == "anthropic/claude-sonnet-4.5"


def test_demo_atlas_surfaces_source_harness_failure(monkeypatch, tmp_path) -> None:
    scenario_file = tmp_path / "atlas.md"
    scenario_file.write_text("Atlas scenario", encoding="utf-8")

    scripts_pkg = types.ModuleType("scripts")
    harness = types.ModuleType("scripts.run_test_big_bang")
    harness.main = lambda argv: 1
    monkeypatch.setitem(sys.modules, "scripts", scripts_pkg)
    monkeypatch.setitem(sys.modules, "scripts.run_test_big_bang", harness)

    result = CliRunner().invoke(main, ["demo", "atlas", "--scenario-file", str(scenario_file)])

    assert result.exit_code == 1
    assert "scripts.run_test_big_bang exited with status 1" in result.output


def test_smoke_live_invokes_source_harness_with_base_url(monkeypatch) -> None:
    calls = []

    scripts_pkg = types.ModuleType("scripts")
    harness = types.ModuleType("scripts.full_runtime_smoke")

    def fake_main():
        calls.append(
            {
                "base_url": cli_main.os.environ.get("WORLDFORK_API_URL"),
                "api_prefix": cli_main.os.environ.get("WORLDFORK_API_PREFIX"),
            }
        )

    harness.main = fake_main
    monkeypatch.setitem(sys.modules, "scripts", scripts_pkg)
    monkeypatch.setitem(sys.modules, "scripts.full_runtime_smoke", harness)
    monkeypatch.delenv("WORLDFORK_API_URL", raising=False)

    result = CliRunner().invoke(
        main,
        ["--base-url", "http://worldfork.test", "--api-prefix", "/custom-api", "smoke", "live"],
    )

    assert result.exit_code == 0
    assert calls == [{"base_url": "http://worldfork.test", "api_prefix": "custom-api"}]
    assert "WORLDFORK_API_URL" not in cli_main.os.environ
    assert "WORLDFORK_API_PREFIX" not in cli_main.os.environ


def test_source_harness_falls_back_to_checkout_subprocess(monkeypatch, tmp_path) -> None:
    calls = []
    harness = tmp_path / "scripts" / "full_runtime_smoke.py"
    harness.parent.mkdir()
    harness.write_text("def main(): pass\n", encoding="utf-8")
    (tmp_path / "backend" / "app").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'worldfork-backend'\n", encoding="utf-8")
    python = tmp_path / ".venv" / ("Scripts/python.exe" if cli_main.os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    def fail_import(_module_name):
        raise ModuleNotFoundError("No module named 'app'")

    def fake_run(command, cwd):
        calls.append((command, cwd))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_main.importlib, "import_module", fail_import)
    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)

    cli_main._run_source_harness("scripts.full_runtime_smoke")

    assert calls == [([str(python), "-m", "scripts.full_runtime_smoke"], tmp_path)]


def test_reports_view_markdown_outputs_markdown(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            assert (method, path, params, json_body) == (
                "GET",
                "/report-versions/rv-123/markdown",
                None,
                None,
            )
            return "# Outcome\n\n## Outcome Distribution\n"

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["reports", "view", "rv-123"])

    assert result.exit_code == 0
    assert "Outcome Distribution" in result.output


def test_query_can_skip_api_prefix(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True):
            calls.append((method, path, params, json_body, use_api_prefix))
            return {"ok": True}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["query", "GET", "/readyz", "--no-api-prefix"])

    assert result.exit_code == 0
    assert calls == [("GET", "/readyz", None, None, False)]


def test_jobs_wait_treats_interrupted_as_non_error_terminal(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            return {
                "ok": True,
                "data": {"id": "job-123", "status": "interrupted"},
                "meta": {"terminal": True, "timed_out": False},
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["jobs", "wait", "job-123", "--timeout", "0"])

    assert result.exit_code == 0


def test_jobs_wait_rejects_zero_poll_interval() -> None:
    result = CliRunner().invoke(main, ["jobs", "wait", "job-123", "--poll-interval", "0"])

    assert result.exit_code == 2
    assert "Invalid value for '--poll-interval'" in result.output


def test_jobs_wait_treats_completed_as_success_terminal(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            return {
                "ok": True,
                "data": {"id": "job-123", "status": "completed"},
                "meta": {"terminal": True, "timed_out": False},
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["jobs", "wait", "job-123", "--timeout", "0"])
    json_result = CliRunner().invoke(main, ["--json", "jobs", "wait", "job-123", "--timeout", "0"])

    assert result.exit_code == 0
    assert json_result.exit_code == 0


def test_jobs_wait_exits_124_when_api_times_out(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            return {
                "ok": True,
                "data": {"id": "job-123", "status": "running"},
                "meta": {"terminal": False, "timed_out": True},
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["jobs", "wait", "job-123", "--timeout", "0"])
    json_result = CliRunner().invoke(main, ["--json", "jobs", "wait", "job-123", "--timeout", "0"])

    assert result.exit_code == 124
    assert json_result.exit_code == 124


def test_jobs_wait_exits_nonzero_for_terminal_unsuccessful_status(monkeypatch) -> None:
    class FakeClient:
        status = "cancelled"

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            return {
                "ok": True,
                "data": {"id": "job-123", "status": self.status},
                "meta": {"terminal": True, "timed_out": False},
            }

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    for status in ("cancelled", "dead_lettered"):
        FakeClient.status = status
        result = CliRunner().invoke(main, ["jobs", "wait", "job-123", "--timeout", "0"])
        json_result = CliRunner().invoke(main, ["--json", "jobs", "wait", "job-123", "--timeout", "0"])

        assert result.exit_code == 2
        assert json_result.exit_code == 2


def test_ledgers_evaluate_wait_accepts_completed_job_status(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            if path == "/big-bangs/bb-123/endpoint-ledgers/evaluate":
                return {"job_id": "job-123", "status": "queued"}
            if path == "/agent/jobs/job-123/wait":
                return {
                    "ok": True,
                    "data": {
                        "id": "job-123",
                        "status": "completed",
                        "result": {"ledger_version_id": "ledger-123"},
                    },
                    "meta": {"terminal": True, "timed_out": False},
                }
            if path == "/endpoint-ledgers/ledger-123":
                return {"id": "ledger-123", "entries": []}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["ledgers", "evaluate", "bb-123", "--wait", "--timeout", "0"])

    assert result.exit_code == 0
    assert calls[-1][1] == "/endpoint-ledgers/ledger-123"


def test_init_blocks_and_returns_initialized_state(monkeypatch, tmp_path) -> None:
    scenario_file = tmp_path / "scenario.md"
    scenario_file.write_text("A small town debates a transit bond.", encoding="utf-8")
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(
            self,
            method,
            path,
            *,
            params=None,
            json_body=None,
            use_api_prefix=True,
            timeout=None,
        ):
            calls.append((method, path, params, json_body, use_api_prefix, timeout))
            if (method, path) == ("POST", "/big-bangs"):
                return {"id": "bb-123", "name": json_body["name"], "status": "running"}
            if path == "/workspace/bb-123/state":
                return {"big_bang": {"id": "bb-123"}, "multiverses": [{"id": "m-1", "status": "active"}], "latest_ticks": []}
            if path == "/big-bangs/bb-123/initialization":
                return {"big_bang_id": "bb-123", "initializer_output": {"simulation_brief": "ready"}}
            if path == "/big-bangs/bb-123/initialization/actors":
                return [{"id": "actor-1", "name": "Residents"}]
            if path == "/big-bangs/bb-123/initialization/traits":
                return []
            if path == "/big-bangs/bb-123/initialization/graphs":
                return {"edges": [], "snapshots": []}
            if path == "/big-bangs/bb-123/initialization/sociology-baseline":
                return {"signals": [], "prompt_influences": []}
            if path == "/big-bangs/bb-123/initialization/emotion-baseline":
                return {"observations": [], "snapshots": []}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--name",
            "Transit Bond",
            "--scenario-file",
            str(scenario_file),
            "--no-initializer-agent",
            "--max-ticks",
            "4",
            "--wait-timeout",
            "123",
        ],
    )

    assert result.exit_code == 0
    assert "initialized_state" in result.output
    assert calls[0][0:2] == ("POST", "/big-bangs")
    assert calls[0][3]["scenario_text"] == "A small town debates a transit bond."
    assert calls[0][3]["simulation_config"]["max_ticks"] == 4
    assert calls[0][3]["use_initializer_agent"] is False
    assert calls[0][5] == 123
    assert [call[1] for call in calls[1:]] == [
        "/workspace/bb-123/state",
        "/big-bangs/bb-123/initialization",
        "/big-bangs/bb-123/initialization/actors",
        "/big-bangs/bb-123/initialization/traits",
        "/big-bangs/bb-123/initialization/graphs",
        "/big-bangs/bb-123/initialization/sociology-baseline",
        "/big-bangs/bb-123/initialization/emotion-baseline",
    ]


def test_watch_big_bang_once_streams_activity_and_logs(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params))
            if path == "/workspace/bb-123/state":
                return {
                    "big_bang": {"id": "bb-123", "name": "Run", "status": "running", "updated_at": "t0"},
                    "multiverses": [{"id": "m-1", "big_bang_id": "bb-123", "ui_label": "M1", "status": "active", "updated_at": "t1"}],
                    "latest_ticks": [{"id": "tick-1", "status": "final", "tick_index": 0, "summary": "root ready", "created_at": "t2"}],
                }
            if path == "/workspace/bb-123/activity":
                return {
                    "ticks": [{"id": "tick-2", "status": "final", "tick_index": 1, "summary": "next", "created_at": "t3"}],
                    "tool_calls": [{"id": "tool-1", "status": "succeeded", "tool_name": "continue_timeline", "created_at": "t4"}],
                }
            if path == "/agent/logs":
                return {"ok": True, "data": [{"id": "log-1", "source": "job", "status": "succeeded", "message": "run_tick", "created_at": "t5"}], "meta": {}}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["watch", "big-bang", "bb-123", "--once"])

    assert result.exit_code == 0
    assert "[big_bang] running Run" in result.output
    assert "[tick] final tick=1 next" in result.output
    assert "[tool_call] succeeded continue_timeline" in result.output
    assert "[log.job] succeeded run_tick" in result.output
    assert calls[-1][2]["run_id"] == "bb-123"


def test_watch_big_bang_stops_on_archived_status(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params))
            if path == "/workspace/bb-123/state":
                return {
                    "big_bang": {"id": "bb-123", "name": "Run", "status": "archived", "updated_at": "t0"},
                    "multiverses": [],
                    "latest_ticks": [],
                }
            if path == "/workspace/bb-123/activity":
                return {"ticks": [], "tool_calls": []}
            if path == "/agent/logs":
                return {"ok": True, "data": [], "meta": {}}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["watch", "big-bang", "bb-123"])

    assert result.exit_code == 0
    assert "[big_bang] archived Run" in result.output
    assert [path for _method, path, _params in calls].count("/workspace/bb-123/state") == 1


def test_watch_multiverse_once_streams_multiverse_ticks_and_logs(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            if path == "/multiverses/m-1":
                return {
                    "id": "m-1",
                    "big_bang_id": "bb-123",
                    "ui_label": "M1",
                    "status": "completed",
                    "updated_at": "t1",
                }
            if path == "/multiverses/m-1/ticks":
                return [{"id": "tick-1", "status": "final", "tick_index": 2, "summary": "done", "created_at": "t2"}]
            if path == "/agent/logs":
                assert params["run_id"] == "bb-123"
                return {"ok": True, "data": [], "meta": {}}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["watch", "multiverse", "m-1", "--once"])

    assert result.exit_code == 0
    assert "[multiverse] completed M1" in result.output
    assert "[tick] final tick=2 done" in result.output


def test_watch_rejects_zero_poll_interval() -> None:
    big_bang = CliRunner().invoke(main, ["watch", "big-bang", "bb-123", "--poll-interval", "0"])
    multiverse = CliRunner().invoke(main, ["watch", "multiverse", "m-1", "--poll-interval", "0"])

    assert big_bang.exit_code == 2
    assert multiverse.exit_code == 2
    assert "Invalid value for '--poll-interval'" in big_bang.output
    assert "Invalid value for '--poll-interval'" in multiverse.output


def test_watch_multiverse_stops_on_merged_status(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params))
            if path == "/multiverses/m-1":
                return {
                    "id": "m-1",
                    "big_bang_id": "bb-123",
                    "ui_label": "M1",
                    "status": "merged",
                    "updated_at": "t1",
                }
            if path == "/multiverses/m-1/ticks":
                return []
            if path == "/agent/logs":
                return {"ok": True, "data": [], "meta": {}}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["watch", "multiverse", "m-1"])

    assert result.exit_code == 0
    assert "[multiverse] merged M1" in result.output
    assert [path for _method, path, _params in calls].count("/multiverses/m-1") == 2


def test_cohort_transcript_accepts_multiverse_id(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path, params))
            return {"entries": []}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(
        main,
        [
            "cohorts",
            "transcript",
            "cohort-1",
            "--multiverse-id",
            "m-1",
            "--from-tick",
            "2",
            "--to-tick",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "GET",
            "/agent/cohorts/cohort-1/transcript",
            {"verbosity": "summary", "multiverse_id": "m-1", "from_tick": 2, "to_tick": 4},
        )
    ]
