from __future__ import annotations

from typing import Any

import click

from worldfork_cli import __version__
from worldfork_cli.client import (
    DEFAULT_API_PREFIX,
    DEFAULT_BASE_URL,
    CliError,
    WorldForkClient,
)
from worldfork_cli.output import emit, unwrap

WAIT_SUCCESS_STATUSES = {"succeeded"}
WAIT_ACCEPTABLE_TERMINAL_STATUSES = {"interrupted"}


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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
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
    """WorldFork CLI, optimized for AI-agent operation."""
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
def models() -> None:
    """Inspect model routing and defaults."""


@models.command("list")
@click.pass_obj
def models_list(ctx: Context) -> None:
    emit(ctx.client.request("GET", "/agent/models"), as_json=ctx.as_json)


@main.command()
@click.argument("method", type=click.Choice(["GET", "POST", "PUT", "PATCH", "DELETE"]))
@click.argument("path")
@click.option("--data", help="Raw JSON request body.")
@click.pass_obj
def query(ctx: Context, method: str, path: str, data: str | None) -> None:
    """Escape hatch for direct backend API calls."""
    import json

    body = json.loads(data) if data else None
    emit(ctx.client.request(method, path, json_body=body), as_json=ctx.as_json)


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
