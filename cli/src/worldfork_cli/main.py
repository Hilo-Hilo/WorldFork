from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click

from worldfork_cli import __version__
from worldfork_cli.client import (
    DEFAULT_API_PREFIX,
    DEFAULT_BASE_URL,
    CliError,
    WorldForkClient,
)
from worldfork_cli.openai_codex_auth import (
    DEFAULT_TIMEOUT_SECONDS,
    OpenAICodexDevicePrompt,
    default_worldfork_codex_auth_file,
    login_openai_codex_device_code,
)
from worldfork_cli.output import emit, unwrap

WAIT_SUCCESS_STATUSES = {"succeeded"}
WAIT_ACCEPTABLE_TERMINAL_STATUSES = {"interrupted"}
RUN_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "terminated"}
MULTIVERSE_TERMINAL_STATUSES = {"completed", "terminated", "frozen", "killed"}


class Context:
    def __init__(self, client: WorldForkClient, as_json: bool, verbosity: str, fields: str | None) -> None:
        self.client = client
        self.as_json = as_json
        self.verbosity = verbosity
        self.fields = fields

    def params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {"verbosity": self.verbosity}
        if self.fields:
            params["fields"] = self.fields
        params.update({k: v for k, v in extra.items() if v is not None})
        return params


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 120}


class _GlobalFlagFloatingGroup(click.Group):
    """Click group that lifts global flags to the front before parsing.

    Click's default parser binds options to the most-recently-seen subcommand,
    so ``worldfork init --json`` errors with "No such option: --json" because
    ``--json`` is declared on the parent group. This subclass scans the raw
    argv and floats known global flags ahead of any subcommand, preserving
    order otherwise, so users can place global flags either before or after
    the subcommand.
    """

    _GLOBAL_FLAGS_NO_VALUE = {"--json"}
    _GLOBAL_FLAGS_WITH_VALUE = {"--base-url", "--api-prefix", "--timeout", "--verbosity", "--fields"}

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        front: list[str] = []
        rest: list[str] = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                rest.extend(args[i:])
                break
            if arg in self._GLOBAL_FLAGS_NO_VALUE:
                front.append(arg)
                i += 1
                continue
            if arg in self._GLOBAL_FLAGS_WITH_VALUE and i + 1 < len(args):
                front.extend([arg, args[i + 1]])
                i += 2
                continue
            if "=" in arg:
                head, _, _ = arg.partition("=")
                if head in self._GLOBAL_FLAGS_NO_VALUE | self._GLOBAL_FLAGS_WITH_VALUE:
                    front.append(arg)
                    i += 1
                    continue
            rest.append(arg)
            i += 1
        return super().parse_args(ctx, front + rest)


@click.group(cls=_GlobalFlagFloatingGroup, context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__)
@click.option("--base-url", default=DEFAULT_BASE_URL, show_default=True, help="Backend root URL.")
@click.option("--api-prefix", default=DEFAULT_API_PREFIX, show_default=True, help="Backend API prefix.")
@click.option("--timeout", type=float, default=30, show_default=True, help="HTTP timeout seconds.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--verbosity",
    type=click.Choice(["summary", "normal", "full"]),
    default="summary",
    show_default=True,
    help="Response detail level for agent endpoints.",
)
@click.option("--fields", help="Comma-separated top-level fields to keep on large rows.")
@click.pass_context
def main(
    ctx: click.Context,
    base_url: str,
    api_prefix: str,
    timeout: float,
    as_json: bool,
    verbosity: str,
    fields: str | None,
) -> None:
    """Operate a WorldFork backend from one command.

    Common flows:

    \b
      worldfork status
      worldfork agent discover
      worldfork init --name "Atlas onboarding" --scenario-file examples/test-big-bang.md
      worldfork watch big-bang <big-bang-id>
      worldfork reports view <report-version-id>
      worldfork settings patch --data '{"default_tick_duration_minutes":90}'
      worldfork smoke live
      worldfork demo atlas

    Global options (--json, --verbosity, --fields, --base-url, --api-prefix,
    --timeout) may appear before or after the subcommand. Use --json for
    scripts, --verbosity summary for compact agent output, and --fields a,b,c
    when a large list should be projected to a few top-level fields.
    """
    ctx.obj = Context(WorldForkClient(base_url, api_prefix, timeout), as_json, verbosity, fields)


@main.command()
@click.pass_obj
def status(ctx: Context) -> None:
    """Show backend and queue status."""
    emit(ctx.client.request("GET", "/agent/status"), as_json=ctx.as_json)


@main.group()
def agent() -> None:
    """Agent-oriented discovery and diagnostics."""


@agent.command()
@click.pass_obj
def discover(ctx: Context) -> None:
    """Print the canonical AI-agent discovery contract."""
    emit(ctx.client.request("GET", "/agent/discover"), as_json=ctx.as_json)


@main.group()
def runs() -> None:
    """List and inspect WorldFork runs."""


def _payload_data(payload: Any) -> Any:
    data, _meta = unwrap(payload)
    return data


def _parse_json_object(value: str | None, label: str) -> dict[str, Any]:
    if not value:
        return {}
    text = _read_json_text(value)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise click.UsageError(f"{label} must decode to a JSON object")
    return parsed


def _parse_json_list(value: str | None, label: str) -> list[dict[str, Any]]:
    if not value:
        return []
    text = _read_json_text(value)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.UsageError(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise click.UsageError(f"{label} must decode to a JSON array of objects")
    return parsed


def _read_json_text(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    if value.lstrip().startswith(("{", "[")):
        return value
    path = Path(value)
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return value
    return value


def _scenario_text(scenario: str | None, scenario_file: Path | None) -> str | None:
    if scenario and scenario_file:
        raise click.UsageError("use either --scenario or --scenario-file, not both")
    if scenario_file:
        return scenario_file.read_text(encoding="utf-8")
    return scenario


def _collect_initialized_state(ctx: Context, big_bang_id: str) -> dict[str, Any]:
    return {
        "workspace": ctx.client.request("GET", f"/workspace/{big_bang_id}/state"),
        "initialization": ctx.client.request("GET", f"/big-bangs/{big_bang_id}/initialization"),
        "actors": ctx.client.request("GET", f"/big-bangs/{big_bang_id}/initialization/actors"),
        "traits": ctx.client.request("GET", f"/big-bangs/{big_bang_id}/initialization/traits"),
        "graphs": ctx.client.request("GET", f"/big-bangs/{big_bang_id}/initialization/graphs"),
        "sociology_baseline": ctx.client.request(
            "GET",
            f"/big-bangs/{big_bang_id}/initialization/sociology-baseline",
        ),
        "emotion_baseline": ctx.client.request(
            "GET",
            f"/big-bangs/{big_bang_id}/initialization/emotion-baseline",
        ),
    }


@main.command("init")
@click.option("--name", required=True, help="Big Bang name.")
@click.option("--description", help="Optional Big Bang description.")
@click.option("--scenario", help="Inline scenario text.")
@click.option(
    "--scenario-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a scenario text or Markdown file.",
)
@click.option("--scenario-input", help="JSON object or @file merged into scenario_input.")
@click.option("--simulation-config", help="JSON object or @file for simulation_config.")
@click.option("--model-config", help="JSON object or @file for model_config.")
@click.option("--branch-policy", help="JSON object or @file for branch_policy.")
@click.option("--actors", help="JSON array or @file for manual actors.")
@click.option("--cohorts", help="JSON array or @file for manual cohorts.")
@click.option("--heroes", help="JSON array or @file for manual heroes.")
@click.option("--max-ticks", type=int, help="Convenience override for simulation_config.max_ticks.")
@click.option(
    "--tick-duration-minutes",
    type=int,
    help="Convenience override for simulation_config.tick_duration_minutes.",
)
@click.option(
    "--max-schedule-horizon-ticks",
    type=int,
    help="Convenience override for simulation_config.max_schedule_horizon_ticks.",
)
@click.option("--initializer-prompt", help="Optional extra prompt text for the initializer agent.")
@click.option(
    "--use-initializer-agent/--no-initializer-agent",
    default=True,
    show_default=True,
    help="Run the initializer LLM agent before returning.",
)
@click.option(
    "--wait-timeout",
    type=float,
    default=600,
    show_default=True,
    help="HTTP timeout seconds for the blocking initialization request.",
)
@click.pass_obj
def init_command(
    ctx: Context,
    name: str,
    description: str | None,
    scenario: str | None,
    scenario_file: Path | None,
    scenario_input: str | None,
    simulation_config: str | None,
    model_config: str | None,
    branch_policy: str | None,
    actors: str | None,
    cohorts: str | None,
    heroes: str | None,
    max_ticks: int | None,
    tick_duration_minutes: int | None,
    max_schedule_horizon_ticks: int | None,
    initializer_prompt: str | None,
    use_initializer_agent: bool,
    wait_timeout: float,
) -> None:
    """Create a Big Bang and return the completed initialized state."""
    sim_config = _parse_json_object(simulation_config, "--simulation-config")
    if max_ticks is not None:
        sim_config["max_ticks"] = max_ticks
    if tick_duration_minutes is not None:
        sim_config["tick_duration_minutes"] = tick_duration_minutes
    if max_schedule_horizon_ticks is not None:
        sim_config["max_schedule_horizon_ticks"] = max_schedule_horizon_ticks

    payload = {
        "name": name,
        "description": description,
        "scenario_text": _scenario_text(scenario, scenario_file),
        "scenario_input": _parse_json_object(scenario_input, "--scenario-input"),
        "simulation_config": sim_config,
        "model_config": _parse_json_object(model_config, "--model-config"),
        "branch_policy": _parse_json_object(branch_policy, "--branch-policy"),
        "actors": _parse_json_list(actors, "--actors"),
        "cohorts": _parse_json_list(cohorts, "--cohorts"),
        "heroes": _parse_json_list(heroes, "--heroes"),
        "use_initializer_agent": use_initializer_agent,
        "initializer_prompt": initializer_prompt,
    }
    created = ctx.client.request("POST", "/big-bangs", json_body=payload, timeout=wait_timeout)
    big_bang_id = str(created["id"])
    emit(
        {
            "ok": True,
            "data": {
                "big_bang": created,
                "initialized_state": _collect_initialized_state(ctx, big_bang_id),
            },
            "meta": {"waited_for_initialization": True},
        },
        as_json=ctx.as_json,
    )


main.add_command(init_command, "initialize")


@runs.command("list")
@click.option("--status")
@click.option("--q")
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_obj
def runs_list(ctx: Context, status: str | None, q: str | None, limit: int, offset: int) -> None:
    emit(
        ctx.client.request(
            "GET",
            "/agent/runs",
            params=ctx.params(status=status, q=q, limit=limit, offset=offset),
        ),
        as_json=ctx.as_json,
    )


@runs.command()
@click.argument("run_id")
@click.pass_obj
def workspace(ctx: Context, run_id: str) -> None:
    """Show a compact run workspace snapshot."""
    emit(
        ctx.client.request("GET", f"/agent/runs/{run_id}/workspace", params=ctx.params()),
        as_json=ctx.as_json,
    )


@runs.command("delete")
@click.argument("run_id")
@click.pass_obj
def runs_delete(ctx: Context, run_id: str) -> None:
    """Soft-delete a run by archiving the canonical Big Bang."""
    emit(ctx.client.request("DELETE", f"/big-bangs/{run_id}"), as_json=ctx.as_json)


@main.group()
def universes() -> None:
    """Inspect universes and per-tick traces."""


@universes.command()
@click.argument("universe_id")
@click.option("--tick", type=int)
@click.option("--actor-id")
@click.option("--actor-kind", type=click.Choice(["cohort", "hero", "actor", "god"]))
@click.pass_obj
def trace(ctx: Context, universe_id: str, tick: int | None, actor_id: str | None, actor_kind: str | None) -> None:
    emit(
        ctx.client.request(
            "GET",
            f"/agent/universes/{universe_id}/trace",
            params=ctx.params(tick=tick, actor_id=actor_id, actor_kind=actor_kind),
        ),
        as_json=ctx.as_json,
    )


@main.group()
def cohorts() -> None:
    """Inspect cohort state over tick ranges."""


@cohorts.command()
@click.argument("cohort_id")
@click.option("--universe-id", required=True)
@click.option("--from-tick", type=int, default=0, show_default=True)
@click.option("--to-tick", type=int, default=10, show_default=True)
@click.pass_obj
def transcript(ctx: Context, cohort_id: str, universe_id: str, from_tick: int, to_tick: int) -> None:
    emit(
        ctx.client.request(
            "GET",
            f"/agent/cohorts/{cohort_id}/transcript",
            params=ctx.params(multiverse_id=universe_id, from_tick=from_tick, to_tick=to_tick),
        ),
        as_json=ctx.as_json,
    )


@main.group()
def jobs() -> None:
    """Inspect and control background jobs."""


@jobs.command("list")
@click.option("--run-id")
@click.option("--status")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_obj
def jobs_list(ctx: Context, run_id: str | None, status: str | None, limit: int, offset: int) -> None:
    emit(
        ctx.client.request(
            "GET",
            "/agent/jobs",
            params=ctx.params(run_id=run_id, status=status, limit=limit, offset=offset),
        ),
        as_json=ctx.as_json,
    )


@jobs.command()
@click.argument("job_id")
@click.option("--timeout", "timeout_seconds", type=float, default=30, show_default=True)
@click.option("--poll-interval", type=float, default=1, show_default=True)
@click.pass_obj
def wait(ctx: Context, job_id: str, timeout_seconds: float, poll_interval: float) -> None:
    payload = ctx.client.request(
        "POST",
        f"/agent/jobs/{job_id}/wait",
        json_body={"timeout_seconds": timeout_seconds, "poll_interval_seconds": poll_interval},
    )
    data, meta = unwrap(payload)
    emit(payload, as_json=ctx.as_json)
    if meta.get("timed_out"):
        raise click.exceptions.Exit(124)
    status = data.get("status") if isinstance(data, dict) else None
    if status == "failed":
        raise click.exceptions.Exit(2)
    if meta.get("terminal") and status not in WAIT_SUCCESS_STATUSES | WAIT_ACCEPTABLE_TERMINAL_STATUSES:
        raise click.exceptions.Exit(2)


def _job_mutation(ctx: Context, job_id: str, action: str) -> None:
    emit(
        ctx.client.request("POST", f"/jobs/{job_id}/{action}"),
        as_json=ctx.as_json,
    )


@jobs.command()
@click.argument("job_id")
@click.pass_obj
def pause(ctx: Context, job_id: str) -> None:
    """Pause a queued job or request interrupt for a running job."""
    _job_mutation(ctx, job_id, "pause")


@jobs.command()
@click.argument("job_id")
@click.pass_obj
def resume(ctx: Context, job_id: str) -> None:
    """Resume a paused job."""
    _job_mutation(ctx, job_id, "resume")


@jobs.command()
@click.argument("job_id")
@click.pass_obj
def interrupt(ctx: Context, job_id: str) -> None:
    """Request interruption for a running job."""
    _job_mutation(ctx, job_id, "interrupt")


@jobs.command()
@click.argument("job_id")
@click.pass_obj
def requeue(ctx: Context, job_id: str) -> None:
    """Requeue an eligible failed or interrupted job."""
    _job_mutation(ctx, job_id, "requeue")


@jobs.command()
@click.argument("job_id")
@click.pass_obj
def claim(ctx: Context, job_id: str) -> None:
    """Claim a queued job for manual execution diagnostics."""
    _job_mutation(ctx, job_id, "claim")


@jobs.command("run")
@click.argument("job_id")
@click.pass_obj
def run_job_command(ctx: Context, job_id: str) -> None:
    """Run a job synchronously through the backend debug endpoint."""
    _job_mutation(ctx, job_id, "run")


@main.group()
def logs() -> None:
    """Inspect unified backend logs."""


@logs.command("list")
@click.option("--run-id")
@click.option("--status")
@click.option("--source", type=click.Choice(["job", "llm"]))
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
@click.pass_obj
def logs_list(
    ctx: Context,
    run_id: str | None,
    status: str | None,
    source: str | None,
    limit: int,
    offset: int,
) -> None:
    emit(
        ctx.client.request(
            "GET",
            "/agent/logs",
            params=ctx.params(run_id=run_id, status=status, source=source, limit=limit, offset=offset),
        ),
        as_json=ctx.as_json,
    )


@main.group()
def watch() -> None:
    """Watch Big Bang or multiverse activity until it finishes."""


@watch.command("big-bang")
@click.argument("big_bang_id")
@click.option("--poll-interval", type=float, default=1, show_default=True)
@click.option("--timeout", "timeout_seconds", type=float, default=0, show_default=True, help="0 means no timeout.")
@click.option("--limit", type=int, default=100, show_default=True, help="Recent log/activity rows to poll.")
@click.option("--once", is_flag=True, help="Print one snapshot and exit.")
@click.option("--json-lines", is_flag=True, help="Emit one JSON object per watched event.")
@click.option("--stop/--no-stop", default=True, show_default=True, help="Stop when the run is terminal.")
@click.pass_obj
def watch_big_bang(
    ctx: Context,
    big_bang_id: str,
    poll_interval: float,
    timeout_seconds: float,
    limit: int,
    once: bool,
    json_lines: bool,
    stop: bool,
) -> None:
    """Stream/poll activity for a Big Bang."""
    _watch_big_bang(ctx, big_bang_id, poll_interval, timeout_seconds, limit, once, json_lines, stop)


@watch.command("multiverse")
@click.argument("multiverse_id")
@click.option("--poll-interval", type=float, default=1, show_default=True)
@click.option("--timeout", "timeout_seconds", type=float, default=0, show_default=True, help="0 means no timeout.")
@click.option("--limit", type=int, default=100, show_default=True, help="Recent log/tick rows to poll.")
@click.option("--once", is_flag=True, help="Print one snapshot and exit.")
@click.option("--json-lines", is_flag=True, help="Emit one JSON object per watched event.")
@click.option("--stop/--no-stop", default=True, show_default=True, help="Stop when the multiverse is terminal.")
@click.pass_obj
def watch_multiverse(
    ctx: Context,
    multiverse_id: str,
    poll_interval: float,
    timeout_seconds: float,
    limit: int,
    once: bool,
    json_lines: bool,
    stop: bool,
) -> None:
    """Stream/poll activity for one multiverse."""
    _watch_multiverse(ctx, multiverse_id, poll_interval, timeout_seconds, limit, once, json_lines, stop)


watch.add_command(watch_big_bang, "run")
watch.add_command(watch_multiverse, "universe")


def _watch_big_bang(
    ctx: Context,
    big_bang_id: str,
    poll_interval: float,
    timeout_seconds: float,
    limit: int,
    once: bool,
    json_lines: bool,
    stop: bool,
) -> None:
    seen: set[str] = set()
    deadline = _deadline(timeout_seconds)
    while True:
        workspace = ctx.client.request("GET", f"/workspace/{big_bang_id}/state")
        _emit_big_bang_snapshot(workspace, seen=seen, json_lines=json_lines)
        activity = ctx.client.request("GET", f"/workspace/{big_bang_id}/activity")
        _emit_activity(activity, seen=seen, json_lines=json_lines)
        logs_payload = ctx.client.request(
            "GET",
            "/agent/logs",
            params=ctx.params(run_id=big_bang_id, limit=limit, verbosity="normal"),
        )
        _emit_logs(_payload_data(logs_payload), seen=seen, json_lines=json_lines)
        if once or (stop and _big_bang_is_done(workspace)):
            return
        _sleep_or_timeout(deadline, poll_interval)


def _watch_multiverse(
    ctx: Context,
    multiverse_id: str,
    poll_interval: float,
    timeout_seconds: float,
    limit: int,
    once: bool,
    json_lines: bool,
    stop: bool,
) -> None:
    seen: set[str] = set()
    deadline = _deadline(timeout_seconds)
    multiverse = ctx.client.request("GET", f"/multiverses/{multiverse_id}")
    big_bang_id = str(multiverse["big_bang_id"])
    while True:
        multiverse = ctx.client.request("GET", f"/multiverses/{multiverse_id}")
        _emit_multiverse_snapshot(multiverse, seen=seen, json_lines=json_lines)
        ticks = ctx.client.request("GET", f"/multiverses/{multiverse_id}/ticks")
        _emit_ticks(ticks, seen=seen, json_lines=json_lines)
        logs_payload = ctx.client.request(
            "GET",
            "/agent/logs",
            params=ctx.params(run_id=big_bang_id, limit=limit, verbosity="normal"),
        )
        _emit_logs(_payload_data(logs_payload), seen=seen, json_lines=json_lines)
        if once or (stop and str(multiverse.get("status")) in MULTIVERSE_TERMINAL_STATUSES):
            return
        _sleep_or_timeout(deadline, poll_interval)


def _deadline(timeout_seconds: float) -> float | None:
    return time.monotonic() + timeout_seconds if timeout_seconds and timeout_seconds > 0 else None


def _sleep_or_timeout(deadline: float | None, poll_interval: float) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise click.exceptions.Exit(124)
    sleep_for = poll_interval
    if deadline is not None:
        sleep_for = max(0.0, min(poll_interval, deadline - time.monotonic()))
    time.sleep(sleep_for)
    if deadline is not None and time.monotonic() >= deadline:
        raise click.exceptions.Exit(124)


def _emit_big_bang_snapshot(workspace: dict[str, Any], *, seen: set[str], json_lines: bool) -> None:
    run = workspace.get("big_bang") or {}
    event = {
        "kind": "big_bang",
        "id": str(run.get("id") or ""),
        "status": run.get("status"),
        "message": run.get("name") or "Big Bang status",
        "created_at": run.get("updated_at") or run.get("created_at"),
    }
    _emit_watch_event(event, key=f"big_bang:{event['id']}:{event['status']}", seen=seen, json_lines=json_lines)
    for multiverse in workspace.get("multiverses") or []:
        _emit_multiverse_snapshot(multiverse, seen=seen, json_lines=json_lines)
    for tick in workspace.get("latest_ticks") or []:
        _emit_tick(tick, seen=seen, json_lines=json_lines)


def _emit_multiverse_snapshot(multiverse: dict[str, Any], *, seen: set[str], json_lines: bool) -> None:
    event = {
        "kind": "multiverse",
        "id": str(multiverse.get("id") or ""),
        "status": multiverse.get("status"),
        "message": multiverse.get("ui_label") or multiverse.get("branch_reason") or "Multiverse status",
        "created_at": multiverse.get("updated_at") or multiverse.get("created_at"),
        "big_bang_id": str(multiverse.get("big_bang_id") or ""),
    }
    _emit_watch_event(
        event,
        key=f"multiverse:{event['id']}:{event['status']}:{multiverse.get('report_status')}",
        seen=seen,
        json_lines=json_lines,
    )


def _emit_activity(activity: dict[str, Any], *, seen: set[str], json_lines: bool) -> None:
    _emit_ticks(activity.get("ticks") or [], seen=seen, json_lines=json_lines)
    for tool_call in reversed(activity.get("tool_calls") or []):
        event = {
            "kind": "tool_call",
            "id": str(tool_call.get("id") or ""),
            "status": tool_call.get("status"),
            "message": tool_call.get("tool_name") or "tool call",
            "created_at": tool_call.get("created_at") or tool_call.get("updated_at"),
            "big_bang_id": str(tool_call.get("big_bang_id") or ""),
            "multiverse_id": str(tool_call.get("multiverse_id") or ""),
        }
        _emit_watch_event(event, key=f"tool:{event['id']}:{event['status']}", seen=seen, json_lines=json_lines)


def _emit_ticks(ticks: list[dict[str, Any]], *, seen: set[str], json_lines: bool) -> None:
    for tick in reversed(ticks):
        _emit_tick(tick, seen=seen, json_lines=json_lines)


def _emit_tick(tick: dict[str, Any], *, seen: set[str], json_lines: bool) -> None:
    event = {
        "kind": "tick",
        "id": str(tick.get("id") or ""),
        "status": tick.get("status"),
        "message": tick.get("summary") or tick.get("ui_label") or f"tick {tick.get('tick_index')}",
        "created_at": tick.get("created_at") or tick.get("updated_at"),
        "big_bang_id": str(tick.get("big_bang_id") or ""),
        "multiverse_id": str(tick.get("multiverse_id") or ""),
        "tick_index": tick.get("tick_index"),
    }
    _emit_watch_event(event, key=f"tick:{event['id']}:{event['status']}", seen=seen, json_lines=json_lines)


def _emit_logs(logs: list[dict[str, Any]], *, seen: set[str], json_lines: bool) -> None:
    for row in reversed(logs or []):
        event = {
            "kind": f"log.{row.get('source') or 'entry'}",
            "id": str(row.get("id") or ""),
            "status": row.get("status"),
            "message": row.get("message") or row.get("job_type") or row.get("source") or "log",
            "created_at": row.get("created_at"),
            "big_bang_id": str(row.get("big_bang_id") or ""),
            "provider": row.get("provider"),
            "model": row.get("model"),
        }
        _emit_watch_event(
            event,
            key=f"log:{row.get('source')}:{event['id']}:{event['status']}",
            seen=seen,
            json_lines=json_lines,
        )


def _emit_watch_event(
    event: dict[str, Any],
    *,
    key: str,
    seen: set[str],
    json_lines: bool,
) -> None:
    if not event.get("id") and not event.get("message"):
        return
    if key in seen:
        return
    seen.add(key)
    if json_lines:
        click.echo(json.dumps(event, default=str, separators=(",", ":")))
        return
    click.echo(_format_watch_event(event))


def _format_watch_event(event: dict[str, Any]) -> str:
    timestamp = event.get("created_at") or ""
    status = event.get("status") or ""
    kind = event.get("kind") or "event"
    message = str(event.get("message") or "").replace("\n", " ")
    parts = [str(timestamp), f"[{kind}]"]
    if status:
        parts.append(str(status))
    if event.get("tick_index") is not None:
        parts.append(f"tick={event['tick_index']}")
    parts.append(message)
    return " ".join(part for part in parts if part)


def _big_bang_is_done(workspace: dict[str, Any]) -> bool:
    run = workspace.get("big_bang") or {}
    if str(run.get("status")) in RUN_TERMINAL_STATUSES:
        return True
    multiverses = workspace.get("multiverses") or []
    return bool(multiverses) and all(
        str(multiverse.get("status")) in MULTIVERSE_TERMINAL_STATUSES for multiverse in multiverses
    )


@main.group()
def reports() -> None:
    """List, inspect, and render structured reports."""


@reports.command("list")
@click.argument("big_bang_id")
@click.pass_obj
def reports_list(ctx: Context, big_bang_id: str) -> None:
    """List report records for a Big Bang."""
    emit(ctx.client.request("GET", f"/big-bangs/{big_bang_id}/reports"), as_json=ctx.as_json)


@reports.command("versions")
@click.argument("report_id")
@click.pass_obj
def reports_versions(ctx: Context, report_id: str) -> None:
    """List versions for one report record."""
    emit(ctx.client.request("GET", f"/reports/{report_id}/versions"), as_json=ctx.as_json)


@reports.command("view")
@click.argument("report_version_id")
@click.option("--format", "output_format", type=click.Choice(["markdown", "json"]), default="markdown", show_default=True)
@click.pass_obj
def reports_view(ctx: Context, report_version_id: str, output_format: str) -> None:
    """View a report version as Markdown or structured JSON."""
    if output_format == "json":
        emit(ctx.client.request("GET", f"/report-versions/{report_version_id}"), as_json=ctx.as_json)
        return
    payload = ctx.client.request("GET", f"/report-versions/{report_version_id}/markdown")
    if ctx.as_json:
        emit(payload, as_json=True)
        return
    click.echo(payload)


@reports.command("render")
@click.argument("report_version_id")
@click.option("--format", "output_format", type=click.Choice(["markdown", "pdf"]), default="pdf", show_default=True)
@click.option("--force", is_flag=True, help="Regenerate even when a cached render artifact exists.")
@click.pass_obj
def reports_render(ctx: Context, report_version_id: str, output_format: str, force: bool) -> None:
    """Render a report version to a cached artifact."""
    emit(
        ctx.client.request(
            "POST",
            f"/report-versions/{report_version_id}/render",
            json_body={"format": output_format, "force": force},
        ),
        as_json=ctx.as_json,
    )


@main.group()
def ledgers() -> None:
    """Inspect and evaluate endpoint ledgers."""


@ledgers.command("list")
@click.argument("big_bang_id")
@click.option("--multiverse-id")
@click.pass_obj
def ledgers_list(ctx: Context, big_bang_id: str, multiverse_id: str | None) -> None:
    """List endpoint ledger versions for a Big Bang or multiverse."""
    path = (
        f"/multiverses/{multiverse_id}/endpoint-ledgers"
        if multiverse_id
        else f"/big-bangs/{big_bang_id}/endpoint-ledgers"
    )
    emit(ctx.client.request("GET", path, params=ctx.params()), as_json=ctx.as_json)


@ledgers.command("view")
@click.argument("ledger_version_id")
@click.pass_obj
def ledgers_view(ctx: Context, ledger_version_id: str) -> None:
    """View one endpoint ledger version with entries."""
    emit(ctx.client.request("GET", f"/endpoint-ledgers/{ledger_version_id}"), as_json=ctx.as_json)


@ledgers.command("evaluate")
@click.argument("big_bang_id")
@click.option("--multiverse-id")
@click.option("--wait", "wait_for_job", is_flag=True, help="Wait for the evaluation job and emit the ledger.")
@click.option("--timeout", "timeout_seconds", type=float, default=120, show_default=True)
@click.option("--idempotency-key")
@click.option("--endpoint", help="JSON object or @file describing a candidate endpoint to add/evaluate.")
@click.pass_obj
def ledgers_evaluate(
    ctx: Context,
    big_bang_id: str,
    multiverse_id: str | None,
    wait_for_job: bool,
    timeout_seconds: float,
    idempotency_key: str | None,
    endpoint: str | None,
) -> None:
    """Create a post-simulation endpoint ledger evaluation job."""
    path = (
        f"/multiverses/{multiverse_id}/endpoint-ledgers/evaluate"
        if multiverse_id
        else f"/big-bangs/{big_bang_id}/endpoint-ledgers/evaluate"
    )
    payload = ctx.client.request(
        "POST",
        path,
        json_body={
            "idempotency_key": idempotency_key,
            "run_inline": False,
            "candidate_endpoint": _parse_json_object(endpoint, "--endpoint") if endpoint else None,
        },
    )
    if not wait_for_job:
        emit(payload, as_json=ctx.as_json)
        return
    job_id = payload.get("job_id") or payload.get("id")
    if not job_id:
        emit(payload, as_json=ctx.as_json)
        return
    waited = ctx.client.request(
        "POST",
        f"/agent/jobs/{job_id}/wait",
        json_body={"timeout_seconds": timeout_seconds, "poll_interval_seconds": 1},
    )
    data, meta = unwrap(waited)
    if meta.get("timed_out"):
        emit(waited, as_json=ctx.as_json)
        raise click.exceptions.Exit(124)
    status = data.get("status") if isinstance(data, dict) else None
    if status == "failed":
        emit(waited, as_json=ctx.as_json)
        raise click.exceptions.Exit(2)
    if meta.get("terminal") and status != "succeeded":
        emit(waited, as_json=ctx.as_json)
        raise click.exceptions.Exit(2)
    ledger_id = ((data or {}).get("result") or {}).get("ledger_version_id") if isinstance(data, dict) else None
    emit(
        ctx.client.request("GET", f"/endpoint-ledgers/{ledger_id}") if ledger_id else waited,
        as_json=ctx.as_json,
    )


@main.group()
def models() -> None:
    """Inspect model routing and defaults."""


@models.command("list")
@click.pass_obj
def models_list(ctx: Context) -> None:
    """Show the default model and per-agent model routing."""
    emit(ctx.client.request("GET", "/agent/models"), as_json=ctx.as_json)


models.add_command(models_list, "defaults")


@main.group()
def settings() -> None:
    """Read and update mutable runtime settings through the API."""


@settings.command("show")
@click.pass_obj
def settings_show(ctx: Context) -> None:
    """Show the current mutable global settings row."""
    emit(ctx.client.request("GET", "/settings"), as_json=ctx.as_json)


@settings.command("patch")
@click.option("--data", required=True, help="JSON object or @file to PATCH into /api/settings.")
@click.pass_obj
def settings_patch(ctx: Context, data: str) -> None:
    """Patch global settings and print the persisted result."""
    emit(
        ctx.client.request("PATCH", "/settings", json_body=_parse_json_object(data, "--data")),
        as_json=ctx.as_json,
    )


@settings.command("branch-policy")
@click.option("--data", help="JSON object or @file to PATCH. Omit to read the branch policy.")
@click.pass_obj
def settings_branch_policy(ctx: Context, data: str | None) -> None:
    """Read or patch the default branch policy settings."""
    if data:
        payload = ctx.client.request(
            "PATCH",
            "/settings/branch-policy",
            json_body=_parse_json_object(data, "--data"),
        )
    else:
        payload = ctx.client.request("GET", "/settings/branch-policy")
    emit(payload, as_json=ctx.as_json)


@settings.command("model-routing")
@click.option("--data", help="JSON object or @file to PATCH. Omit to read model routing rows.")
@click.pass_obj
def settings_model_routing(ctx: Context, data: str | None) -> None:
    """Read or patch model routing settings."""
    if data:
        payload = ctx.client.request(
            "PATCH",
            "/settings/model-routing",
            json_body=_parse_json_object(data, "--data"),
        )
    else:
        payload = ctx.client.request("GET", "/settings/model-routing")
    emit(payload, as_json=ctx.as_json)


@settings.command("providers")
@click.option("--data", help="JSON object or @file to PATCH. Omit to read provider rows.")
@click.pass_obj
def settings_providers(ctx: Context, data: str | None) -> None:
    """Read or patch provider settings."""
    if data:
        payload = ctx.client.request(
            "PATCH",
            "/settings/providers",
            json_body=_parse_json_object(data, "--data"),
        )
    else:
        payload = ctx.client.request("GET", "/settings/providers")
    emit(payload, as_json=ctx.as_json)


@settings.command("provider-test")
@click.argument("provider")
@click.pass_obj
def settings_provider_test(ctx: Context, provider: str) -> None:
    """Run the configured provider healthcheck."""
    emit(
        ctx.client.request("POST", "/settings/providers/test", json_body={"provider": provider}),
        as_json=ctx.as_json,
    )


@settings.command("openai-codex-login")
@click.option(
    "--auth-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write OAuth tokens. Defaults to ~/.worldfork/openai-codex-auth.json.",
)
@click.option("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, show_default=True)
@click.pass_obj
def settings_openai_codex_login(ctx: Context, auth_file: Path | None, timeout: int) -> None:
    """Log in to OpenAI Codex with a headless device-code flow."""

    def show_prompt(prompt: OpenAICodexDevicePrompt) -> None:
        message = "\n".join(
            [
                "Open this URL in a browser and enter the code:",
                prompt.verification_url,
                f"Code: {prompt.user_code}",
                f"Expires in: {max(1, round(prompt.expires_in_seconds / 60))} minutes",
            ]
        )
        click.echo(message, err=ctx.as_json)

    target = auth_file or default_worldfork_codex_auth_file()
    result = login_openai_codex_device_code(
        auth_file=target,
        timeout_seconds=timeout,
        on_verification=show_prompt,
    )
    emit(
        {
            "provider": "openai-codex",
            "auth_file": str(result.auth_file),
            "token_present": True,
            "expires_at": result.expires_at,
        },
        as_json=ctx.as_json,
    )


@settings.command("rate-limits")
@click.option("--data", help="JSON object or @file to PATCH. Omit to read provider rate limits.")
@click.pass_obj
def settings_rate_limits(ctx: Context, data: str | None) -> None:
    """Read or patch provider rate-limit settings."""
    if data:
        payload = ctx.client.request(
            "PATCH",
            "/settings/rate-limits",
            json_body=_parse_json_object(data, "--data"),
        )
    else:
        payload = ctx.client.request("GET", "/settings/rate-limits")
    emit(payload, as_json=ctx.as_json)


@main.group()
def demo() -> None:
    """Run built-in demonstration workflows from the WorldFork CLI."""


@demo.command("atlas")
@click.option(
    "--scenario-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Markdown scenario dossier. Defaults to examples/test-big-bang.md in a source checkout.",
)
@click.option("--timeout", type=float, default=240.0, show_default=True, help="HTTP timeout seconds.")
@click.option(
    "--tick-duration-minutes",
    type=int,
    default=720,
    show_default=True,
    help="Simulated minutes per Atlas tick.",
)
@click.option(
    "--horizon-days",
    type=float,
    default=30.0,
    show_default=True,
    help="Target simulated horizon used when --max-tick-index is omitted.",
)
@click.option("--max-tick-index", type=int, help="Terminal tick index for every Atlas timeline.")
@click.option("--max-active-multiverses", type=int, default=64, show_default=True)
@click.option("--max-branch-depth", type=int, default=8, show_default=True)
@click.option("--max-branches-per-tick", type=int, default=8, show_default=True)
@click.option(
    "--branch-score-threshold",
    type=float,
    default=0.4,
    show_default=True,
    help="Lower values admit more God-agent branches.",
)
@click.option("--idle-termination-ticks", type=int, default=6, show_default=True)
@click.option("--completion-max-requests", type=int, default=1000, show_default=True)
@click.option("--expected-provider", default="openrouter", show_default=True)
@click.option("--expected-model", default="google/gemini-3.1-flash-lite-preview", show_default=True)
@click.pass_obj
def demo_atlas(
    ctx: Context,
    scenario_file: Path | None,
    timeout: float,
    tick_duration_minutes: int,
    horizon_days: float,
    max_tick_index: int | None,
    max_active_multiverses: int,
    max_branch_depth: int,
    max_branches_per_tick: int,
    branch_score_threshold: float,
    idle_termination_ticks: int,
    completion_max_requests: int,
    expected_provider: str,
    expected_model: str,
) -> None:
    """Run the full Atlas onboarding multiverse demo.

    The demo creates a Big Bang, runs root and branch timelines, permits
    God-agent-created branches within the supplied caps, drains all timelines
    to terminal state, generates per-multiverse reports, creates the final
    report-agent summary, can render a PDF on request, and audits expected
    provider/model use.
    """
    argv = [
        "--base-url",
        ctx.client.base_url,
        "--timeout",
        str(timeout),
        "--tick-duration-minutes",
        str(tick_duration_minutes),
        "--horizon-days",
        str(horizon_days),
        "--max-active-multiverses",
        str(max_active_multiverses),
        "--max-branch-depth",
        str(max_branch_depth),
        "--max-branches-per-tick",
        str(max_branches_per_tick),
        "--branch-score-threshold",
        str(branch_score_threshold),
        "--idle-termination-ticks",
        str(idle_termination_ticks),
        "--completion-max-requests",
        str(completion_max_requests),
        "--expected-provider",
        expected_provider,
        "--expected-model",
        expected_model,
    ]
    if scenario_file is not None:
        argv.extend(["--scenario-file", str(scenario_file.resolve())])
    if max_tick_index is not None:
        argv.extend(["--max-tick-index", str(max_tick_index)])
    _run_source_harness("scripts.run_test_big_bang", argv=argv)


@main.group()
def smoke() -> None:
    """Run live validation workflows from the WorldFork CLI."""


@smoke.command("live")
@click.pass_obj
def smoke_live(ctx: Context) -> None:
    """Run the full live runtime smoke against the configured backend.

    This validates readiness, settings mutation/restoration, manual branch
    intervention, runtime checkpoints, job control, reports, PDF rendering,
    logs, and Gemini 3.1 Flash Lite audited LLM usage.
    """
    previous = os.environ.get("WORLDFORK_API_URL")
    os.environ["WORLDFORK_API_URL"] = ctx.client.base_url
    try:
        _run_source_harness("scripts.full_runtime_smoke")
    finally:
        if previous is None:
            os.environ.pop("WORLDFORK_API_URL", None)
        else:
            os.environ["WORLDFORK_API_URL"] = previous


def _run_source_harness(module_name: str, argv: list[str] | None = None) -> None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        source_root = _find_source_checkout(module_name)
        if source_root is not None:
            _run_source_harness_subprocess(source_root, module_name, argv)
            return
        raise click.ClickException(
            "This command needs the WorldFork source checkout with backend dependencies installed. "
            "Run it from the repository after setup, or pass the same workflow through a backend environment "
            "that exposes the source harnesses."
        ) from exc
    entrypoint = getattr(module, "main", None)
    if not callable(entrypoint):
        raise click.ClickException(f"{module_name} does not expose a callable main()")
    result = entrypoint(argv) if argv is not None else entrypoint()
    if isinstance(result, int) and result != 0:
        raise click.ClickException(f"{module_name} exited with status {result}")


def _find_source_checkout(module_name: str) -> Path | None:
    harness_path = Path(*module_name.split(".")).with_suffix(".py")
    for root in (Path.cwd(), *Path.cwd().parents):
        if (
            (root / harness_path).is_file()
            and (root / "pyproject.toml").is_file()
            and (root / "backend" / "app").is_dir()
        ):
            return root
    return None


def _run_source_harness_subprocess(
    source_root: Path,
    module_name: str,
    argv: list[str] | None = None,
) -> None:
    python = source_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        command = [str(python), "-m", module_name]
    elif uv := shutil.which("uv"):
        command = [uv, "run", "python", "-m", module_name]
    else:
        command = [sys.executable, "-m", module_name]

    command.extend(argv or [])
    result = subprocess.run(command, cwd=source_root)
    if result.returncode != 0:
        raise click.ClickException(f"{module_name} exited with status {result.returncode}")


@main.command()
@click.argument("method", type=click.Choice(["GET", "POST", "PUT", "PATCH", "DELETE"]))
@click.argument("path")
@click.option("--data", help="Raw JSON request body.")
@click.option("--no-api-prefix", is_flag=True, help="Use the path exactly as root-relative instead of adding /api.")
@click.pass_obj
def query(ctx: Context, method: str, path: str, data: str | None, no_api_prefix: bool) -> None:
    """Escape hatch for direct backend API calls."""
    import json

    body = json.loads(data) if data else None
    emit(ctx.client.request(method, path, json_body=body, use_api_prefix=not no_api_prefix), as_json=ctx.as_json)


def cli() -> None:
    try:
        main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except CliError as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(exc.exit_code) from exc


if __name__ == "__main__":
    cli()
