from __future__ import annotations

import worldfork_cli.main as cli_main
from click.testing import CliRunner
from worldfork_cli.main import main


def test_help_lists_agent_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "agent" in result.output
    assert "runs" in result.output
    assert "jobs" in result.output


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
