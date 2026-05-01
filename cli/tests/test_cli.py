import sys
import types

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
    assert "demo" in result.output
    assert "smoke" in result.output
    assert "demo atlas" in result.output


def test_global_verbosity_parses_before_command() -> None:
    result = CliRunner().invoke(main, ["--verbosity", "normal", "agent", "--help"])

    assert result.exit_code == 0
    assert "discover" in result.output


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


def test_reports_render_calls_report_version_endpoint(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"report_version_id": "rv-123", "format": "pdf", "artifact_id": "artifact-123"}

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["reports", "render", "rv-123", "--format", "pdf"])

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/report-versions/rv-123/render",
            None,
            {"format": "pdf", "force": False},
        )
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


def test_models_defaults_alias_calls_agent_models(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None):
            calls.append((method, path, params, json_body))
            return {"default_model": "google/gemini-3.1-flash-lite-preview"}

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
    assert calls[0][:2] == ["--base-url", "http://worldfork.test"]
    assert ["--scenario-file", str(scenario_file.resolve())] == calls[0][-4:-2]
    assert calls[0][-2:] == ["--max-tick-index", "4"]


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
        calls.append({"base_url": cli_main.os.environ.get("WORLDFORK_API_URL")})

    harness.main = fake_main
    monkeypatch.setitem(sys.modules, "scripts", scripts_pkg)
    monkeypatch.setitem(sys.modules, "scripts.full_runtime_smoke", harness)
    monkeypatch.delenv("WORLDFORK_API_URL", raising=False)

    result = CliRunner().invoke(main, ["--base-url", "http://worldfork.test", "smoke", "live"])

    assert result.exit_code == 0
    assert calls == [{"base_url": "http://worldfork.test"}]
    assert "WORLDFORK_API_URL" not in cli_main.os.environ


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


def test_initialize_alias_uses_init_command(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def request(self, method, path, *, params=None, json_body=None, use_api_prefix=True, timeout=None):
            calls.append((method, path))
            if path == "/big-bangs":
                return {"id": "bb-123", "name": "Alias", "status": "running"}
            if path == "/workspace/bb-123/state":
                return {"big_bang": {"id": "bb-123"}, "multiverses": [], "latest_ticks": []}
            if path == "/big-bangs/bb-123/initialization":
                return {}
            if path.endswith("/actors"):
                return []
            if path.endswith("/traits"):
                return []
            if path.endswith("/graphs"):
                return {"edges": [], "snapshots": []}
            if path.endswith("/sociology-baseline"):
                return {"signals": [], "prompt_influences": []}
            if path.endswith("/emotion-baseline"):
                return {"observations": [], "snapshots": []}
            raise AssertionError(path)

    monkeypatch.setattr(cli_main, "WorldForkClient", FakeClient)

    result = CliRunner().invoke(main, ["initialize", "--name", "Alias", "--scenario", "hello"])

    assert result.exit_code == 0
    assert calls[0] == ("POST", "/big-bangs")


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
