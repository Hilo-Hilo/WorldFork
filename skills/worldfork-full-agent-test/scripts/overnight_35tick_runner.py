"""Continue full-runtime accuracy cases to a 35-tick gate with telemetry.

This harness is intentionally driven by environment variables so it can resume
an existing disposable WorldFork stack without hard-coded machine paths.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import threading
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _default_artifact_root(repo: Path) -> Path:
    explicit = os.environ.get("WF_ARTIFACT_ROOT")
    if explicit:
        return Path(explicit).resolve()
    root = repo / "agent-testing" / "full-runtime-accuracy"
    candidates = [
        path
        for path in root.glob("*")
        if path.is_dir() and (path / "full-runtime-cases.jsonl").exists()
    ]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime).resolve()
    return (root / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")).resolve()


def _json_env(name: str, default: dict[str, Any]) -> dict[str, Any]:
    value = os.environ.get(name)
    if not value:
        return default
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


REPO = Path(os.environ.get("WF_REPO", Path.cwd())).resolve()
ART = _default_artifact_root(REPO)
RUN_ID = os.environ.get("WF_OVERNIGHT_RUN_ID") or datetime.now(UTC).strftime(
    "overnight-35tick-%Y%m%dT%H%M%SZ"
)
RUN_DIR = Path(os.environ.get("WF_OVERNIGHT_DIR", str(ART / RUN_ID))).resolve()
PROJECT = os.environ.get("WF_COMPOSE_PROJECT", f"worldfork-full-agent-test-{RUN_ID}")
BASE_URL = os.environ.get("WF_BASE_URL", "http://localhost:18044")
TARGET_MAX_TICKS = int(os.environ.get("WF_TARGET_MAX_TICKS", "35"))
CONFIGURED_CONCURRENCY = max(1, int(os.environ.get("WF_OVERNIGHT_CONCURRENCY", "4")))
MAX_CONCURRENCY = max(1, int(os.environ.get("WF_OVERNIGHT_MAX_CONCURRENCY", str(CONFIGURED_CONCURRENCY))))
INITIAL_CONCURRENCY = min(CONFIGURED_CONCURRENCY, MAX_CONCURRENCY)
CONCURRENCY_SCALE_AFTER_SUCCESSES = max(1, int(os.environ.get("WF_OVERNIGHT_SCALE_AFTER_SUCCESSES", "2")))
RUN_UNTIL_COMPLETE_RETRIES = max(10, int(os.environ.get("WF_RUN_UNTIL_COMPLETE_RETRIES", "10")))
RUN_UNTIL_COMPLETE_RETRY_DELAY_SECONDS = float(os.environ.get("WF_RUN_UNTIL_COMPLETE_RETRY_DELAY_SECONDS", "15"))
RATE_LIMIT_COOLDOWN_SECONDS = float(
    os.environ.get(
        "WF_RATE_LIMIT_COOLDOWN_SECONDS",
        str(max(RUN_UNTIL_COMPLETE_RETRY_DELAY_SECONDS, 60.0)),
    )
)
SINGLE_MODEL_OVERRIDE = os.environ.get("WF_MODEL", "").strip()
OPENROUTER_MODEL = os.environ.get("WF_OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
OPENAI_CODEX_MODEL = os.environ.get("WF_OPENAI_CODEX_MODEL", "gpt-5.4")
DEFAULT_MODEL = SINGLE_MODEL_OVERRIDE or OPENROUTER_MODEL
FALLBACK_MODEL = SINGLE_MODEL_OVERRIDE or OPENROUTER_MODEL
INITIALIZER_AGENT_MODEL = SINGLE_MODEL_OVERRIDE or OPENAI_CODEX_MODEL
GOD_AGENT_MODEL = SINGLE_MODEL_OVERRIDE or OPENAI_CODEX_MODEL
COHORT_AGENT_MODEL = SINGLE_MODEL_OVERRIDE or OPENROUTER_MODEL
HERO_AGENT_MODEL = SINGLE_MODEL_OVERRIDE or OPENROUTER_MODEL
EVENT_SUMMARY_MODEL = SINGLE_MODEL_OVERRIDE or OPENROUTER_MODEL
REPORT_AGENT_MODEL = SINGLE_MODEL_OVERRIDE or OPENAI_CODEX_MODEL
MODEL = SINGLE_MODEL_OVERRIDE or (
    f"openrouter/{OPENROUTER_MODEL} + openai-codex/{OPENAI_CODEX_MODEL}"
)
BRANCH_POLICY = _json_env("WF_BRANCH_POLICY", {})
CASES_FILE = Path(os.environ.get("WF_CASES_FILE", str(ART / "full-runtime-cases.jsonl"))).resolve()
RENDER_REPORT_PDFS = os.environ.get("WF_RENDER_REPORT_PDFS", "0") == "1"
COPY_RENDERED_ARTIFACTS = os.environ.get("WF_COPY_RENDERED_ARTIFACTS", "0") == "1"
RENDERED_COPY_LIMIT_MIB = int(os.environ.get("WF_RENDERED_COPY_LIMIT_MIB", "1024"))
COMPOSE_OVERRIDE = Path(
    os.environ.get("WF_COMPOSE_OVERRIDE", str(ART / "docker-compose.override.runtime.yml"))
).resolve()
REQUIRE_COMPOSE_OVERRIDE = os.environ.get("WF_REQUIRE_COMPOSE_OVERRIDE", "0") == "1"
COMPOSE_FILES = ["-f", "docker-compose.yml"]
if COMPOSE_OVERRIDE.exists():
    COMPOSE_FILES.extend(["-f", str(COMPOSE_OVERRIDE)])
STOP_MONITOR = threading.Event()
WRITE_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(RUN_DIR))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, sort_keys=True, default=str) + "\n"
    with WRITE_LOCK:
        with path.open("a") as fh:
            fh.write(line)


def run(
    args: list[str],
    *,
    cwd: Path = REPO,
    timeout: int | None = None,
    out: Path | None = None,
    allow_fail: bool = False,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "WORLD_FORK_API_BASE": BASE_URL,
            "BACKEND_API_BASE": BASE_URL,
            "DEFAULT_MODEL": DEFAULT_MODEL,
            "FALLBACK_MODEL": FALLBACK_MODEL,
            "INITIALIZER_AGENT_MODEL": INITIALIZER_AGENT_MODEL,
            "GOD_AGENT_MODEL": GOD_AGENT_MODEL,
            "COHORT_AGENT_MODEL": COHORT_AGENT_MODEL,
            "HERO_AGENT_MODEL": HERO_AGENT_MODEL,
            "EVENT_SUMMARY_MODEL": EVENT_SUMMARY_MODEL,
            "REPORT_AGENT_MODEL": REPORT_AGENT_MODEL,
            "PATH": (
                f"{REPO / '.agent-venv/bin'}:"
                "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
                f"{env.get('PATH', '')}"
            ),
        }
    )
    if env_extra:
        env.update(env_extra)
    started = time.time()
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    duration = time.time() - started
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(proc.stdout)
        if proc.stderr:
            out.with_suffix(out.suffix + ".stderr").write_text(proc.stderr)
        append_jsonl(
            RUN_DIR / "command-results.jsonl",
            {
                "timestamp": now_iso(),
                "command": " ".join(args),
                "exit_code": proc.returncode,
                "duration_seconds": round(duration, 3),
                "stdout_path": rel(out),
                "stderr_path": rel(out.with_suffix(out.suffix + ".stderr")) if proc.stderr else None,
            },
        )
    if proc.returncode and not allow_fail:
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(args)}\n{proc.stderr[:2000]}")
    return proc


def compose(args: list[str], *, timeout: int | None = None, out: Path | None = None, allow_fail: bool = False):
    return run(["docker", "compose", "-p", PROJECT, *COMPOSE_FILES, *args], timeout=timeout, out=out, allow_fail=allow_fail)


def cli(args: list[str], *, timeout: int | None = None, out: Path | None = None, allow_fail: bool = False):
    return run(["worldfork", "--json", "--verbosity", "normal", "--timeout", str(timeout or 120), *args], timeout=timeout, out=out, allow_fail=allow_fail)


def cli_json(args: list[str], *, timeout: int | None = None, out: Path | None = None, allow_fail: bool = False) -> Any:
    proc = cli(args, timeout=timeout, out=out, allow_fail=allow_fail)
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def data(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        return payload["data"]
    return payload


def rows_from_payload(payload: Any) -> list[dict[str, Any]]:
    payload = data(payload)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "ledgers", "versions", "reports", "jobs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def preflight() -> None:
    checks = {
        "repo_exists": REPO.exists(),
        "artifact_root_exists": ART.exists(),
        "cases_file_exists": CASES_FILE.exists(),
        "compose_override_exists": COMPOSE_OVERRIDE.exists(),
        "compose_override_requirement_satisfied": not REQUIRE_COMPOSE_OVERRIDE or COMPOSE_OVERRIDE.exists(),
        "docker_cli": run(["docker", "version"], out=RUN_DIR / "preflight" / "docker-version.txt", timeout=60, allow_fail=True).returncode == 0,
        "worldfork_cli": run(["worldfork", "--help"], out=RUN_DIR / "preflight" / "worldfork-help.txt", timeout=60, allow_fail=True).returncode == 0,
    }
    write_json(RUN_DIR / "preflight.json", checks)
    failed = [
        name
        for name, ok in checks.items()
        if not ok and (name != "compose_override_exists" or REQUIRE_COMPOSE_OVERRIDE)
    ]
    if failed:
        raise RuntimeError(f"preflight failed: {failed}")


def start_stack() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    compose(["up", "-d"], timeout=900, out=RUN_DIR / "setup" / "compose-up.log")
    compose(["ps"], timeout=120, out=RUN_DIR / "setup" / "compose-ps-start.txt")
    for attempt in range(90):
        proc = run(["curl", "-fsS", f"{BASE_URL}/readyz"], timeout=10, out=RUN_DIR / "setup" / f"ready-{attempt:02d}.json", allow_fail=True)
        if proc.returncode == 0:
            try:
                if json.loads(proc.stdout).get("ok"):
                    return
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    raise RuntimeError("backend did not become ready")


def record_before() -> None:
    compose(["ps"], out=RUN_DIR / "docker-compose-ps-before.txt", allow_fail=True)
    ids = compose(["ps", "-q"], out=RUN_DIR / "docker-container-ids.txt", allow_fail=True).stdout.strip()
    run(["docker", "system", "df", "-v"], out=RUN_DIR / "docker-system-df-before.txt", allow_fail=True)
    run(["df", "-h"], out=RUN_DIR / "host-disk-before.txt", allow_fail=True)
    run(["du", "-sh", "runs", "artifacts", "agent-testing"], out=RUN_DIR / "worktree-disk-before.txt", allow_fail=True)
    write_json(
        RUN_DIR / "run-config.json",
        {
            "run_id": RUN_ID,
            "repo": str(REPO),
            "artifact_root": str(ART),
            "cases_file": str(CASES_FILE),
            "run_dir": str(RUN_DIR),
            "compose_project": PROJECT,
            "compose_files": COMPOSE_FILES,
            "compose_override": str(COMPOSE_OVERRIDE),
            "base_url": BASE_URL,
            "target_max_ticks": TARGET_MAX_TICKS,
            "concurrency": INITIAL_CONCURRENCY,
            "configured_concurrency": CONFIGURED_CONCURRENCY,
            "initial_concurrency": INITIAL_CONCURRENCY,
            "max_concurrency": MAX_CONCURRENCY,
            "concurrency_scale_after_successes": CONCURRENCY_SCALE_AFTER_SUCCESSES,
            "run_until_complete_retries": RUN_UNTIL_COMPLETE_RETRIES,
            "run_until_complete_retry_delay_seconds": RUN_UNTIL_COMPLETE_RETRY_DELAY_SECONDS,
            "rate_limit_cooldown_seconds": RATE_LIMIT_COOLDOWN_SECONDS,
            "model": MODEL,
            "openrouter_model": OPENROUTER_MODEL,
            "openai_codex_model": OPENAI_CODEX_MODEL,
            "render_report_pdfs": RENDER_REPORT_PDFS,
            "copy_rendered_artifacts": COPY_RENDERED_ARTIFACTS,
            "rendered_copy_limit_mib": RENDERED_COPY_LIMIT_MIB,
            "requested_branch_policy": BRANCH_POLICY,
            "branch_policy_note": (
                "Continuation inherits the existing BigBangConfig branch_policy. "
                "Use fresh worldfork init runs for branch-policy experiments."
            ),
            "container_ids": ids.splitlines(),
            "started_at": now_iso(),
        },
    )


def monitor_loop() -> None:
    stats_path = RUN_DIR / "docker-stats.jsonl"
    inspect_path = RUN_DIR / "docker-inspect.jsonl"
    ps_path = RUN_DIR / "docker-ps.jsonl"
    while not STOP_MONITOR.is_set():
        timestamp = now_iso()
        ids_proc = compose(["ps", "-q"], allow_fail=True)
        ids = [line for line in ids_proc.stdout.splitlines() if line.strip()]
        if ids:
            stats = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids], allow_fail=True)
            for line in stats.stdout.splitlines():
                if line.strip():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        row = {"raw": line}
                    row["timestamp"] = timestamp
                    append_jsonl(stats_path, row)
            inspect = run(["docker", "inspect", *ids], allow_fail=True)
            try:
                for row in json.loads(inspect.stdout or "[]"):
                    append_jsonl(
                        inspect_path,
                        {
                            "timestamp": timestamp,
                            "name": row.get("Name"),
                            "id": row.get("Id"),
                            "state": row.get("State"),
                            "restart_count": row.get("RestartCount"),
                            "health": (row.get("State") or {}).get("Health"),
                            "mounts": row.get("Mounts"),
                        },
                    )
            except json.JSONDecodeError:
                append_jsonl(inspect_path, {"timestamp": timestamp, "raw": inspect.stdout})
        ps = compose(["ps", "--format", "json"], allow_fail=True)
        if ps.stdout.strip():
            for line in ps.stdout.splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    row = {"raw": line}
                row["timestamp"] = timestamp
                append_jsonl(ps_path, row)
        time.sleep(30)


def start_monitors() -> tuple[threading.Thread, subprocess.Popen[str] | None]:
    thread = threading.Thread(target=monitor_loop, name="docker-resource-monitor", daemon=True)
    thread.start()
    events_file = (RUN_DIR / "docker-events.log").open("a")
    proc: subprocess.Popen[str] | None = None
    event_env = os.environ.copy()
    event_env["PATH"] = (
        f"{REPO / '.agent-venv/bin'}:"
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:"
        f"{event_env.get('PATH', '')}"
    )
    try:
        proc = subprocess.Popen(
            [
                "docker",
                "events",
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
                "--format",
                "{{json .}}",
            ],
            cwd=REPO,
            env=event_env,
            text=True,
            stdout=events_file,
            stderr=subprocess.STDOUT,
        )
        (RUN_DIR / "docker-events.pid").write_text(str(proc.pid) + "\n")
    except Exception as exc:
        append_jsonl(RUN_DIR / "monitor-errors.jsonl", {"timestamp": now_iso(), "error": str(exc)})
    return thread, proc


def stop_monitors(thread: threading.Thread, events_proc: subprocess.Popen[str] | None) -> None:
    STOP_MONITOR.set()
    thread.join(timeout=45)
    if events_proc and events_proc.poll() is None:
        events_proc.terminate()
        try:
            events_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            events_proc.kill()
    compose(["ps"], out=RUN_DIR / "docker-compose-ps-after.txt", allow_fail=True)
    ids_path = RUN_DIR / "docker-container-ids.txt"
    ids = ids_path.read_text().splitlines() if ids_path.exists() else []
    if ids:
        run(["docker", "inspect", *ids], out=RUN_DIR / "docker-inspect-final.json", allow_fail=True)
    run(["docker", "system", "df", "-v"], out=RUN_DIR / "docker-system-df-after.txt", allow_fail=True)
    run(["df", "-h"], out=RUN_DIR / "host-disk-after.txt", allow_fail=True)
    run(["du", "-sh", "runs", "artifacts", "agent-testing"], out=RUN_DIR / "worktree-disk-after.txt", allow_fail=True)


def load_cases() -> list[dict[str, Any]]:
    rows = []
    for line in CASES_FILE.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == "case_result":
            rows.append(
                {
                    "case_id": row["case_id"],
                    "category": row["category"],
                    "big_bang_id": row["ids"]["big_bang_id"],
                    "root_multiverse_id": row["ids"]["root_multiverse_id"],
                }
            )
    return rows


def latest_tick_index(multiverse_id: str, case_dir: Path, label: str) -> int:
    ticks = data(cli_json(["query", "GET", f"/api/multiverses/{multiverse_id}/ticks"], out=case_dir / f"{label}_ticks.json", timeout=180))
    if not ticks:
        return -1
    return max(int(row.get("tick_index") or 0) for row in ticks)


def record_config_snapshot(big_bang_id: str, case_dir: Path) -> None:
    sql = (
        "select coalesce(json_agg(json_build_object("
        "'version', version, "
        "'simulation_config', simulation_config, "
        "'branch_policy', branch_policy, "
        "'model_config', model_config"
        ") order by version)::text, '[]') "
        f"from big_bang_configs where big_bang_id = '{big_bang_id}'::uuid;"
    )
    compose(
        ["exec", "-T", "postgres", "psql", "-U", "worldfork", "-d", "worldfork", "-tA", "-c", sql],
        out=case_dir / "config_versions.json",
        timeout=180,
        allow_fail=True,
    )


def collect_case_artifacts(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    bb = case["big_bang_id"]
    record_config_snapshot(bb, case_dir)
    workspace = data(cli_json(["runs", "workspace", bb], out=case_dir / "workspace.json", timeout=180))
    multiverses = data(cli_json(["query", "GET", f"/api/big-bangs/{bb}/multiverses"], out=case_dir / "multiverses.json", timeout=180))
    cli(["watch", "big-bang", bb, "--once"], out=case_dir / "watch_big_bang.jsonl", timeout=180, allow_fail=True)
    cli(["logs", "list", "--run-id", bb, "--source", "llm", "--limit", "500"], out=case_dir / "llm_logs.json", timeout=180, allow_fail=True)
    cli(["logs", "list", "--run-id", bb, "--status", "failed", "--limit", "500"], out=case_dir / "failed_logs.json", timeout=180, allow_fail=True)
    cli(["jobs", "list", "--run-id", bb, "--limit", "500"], out=case_dir / "jobs.json", timeout=180, allow_fail=True)
    cli(["jobs", "list", "--run-id", bb, "--status", "failed", "--limit", "500"], out=case_dir / "failed_jobs.json", timeout=180, allow_fail=True)
    ledgers = data(cli_json(["ledgers", "list", bb], out=case_dir / "ledgers_list.json", timeout=180, allow_fail=True)) or []
    ledger_version_ids: list[str] = []
    for ledger in rows_from_payload(ledgers):
        ledger_id = ledger.get("id") or ledger.get("ledger_version_id")
        if ledger_id:
            ledger_version_ids.append(str(ledger_id))
    for ledger_id in sorted(set(ledger_version_ids)):
        cli_json(["ledgers", "view", ledger_id], out=case_dir / f"ledger_{ledger_id}.json", timeout=180, allow_fail=True)
    cli_json(["reports", "adjudicate", bb], out=case_dir / "timeline_adjudicate.json", timeout=240, allow_fail=True)
    cli_json(["reports", "adjudication", bb], out=case_dir / "timeline_adjudication_latest.json", timeout=180, allow_fail=True)
    cli_json(["reports", "pack", bb, "--mode", "summary"], out=case_dir / "report_evidence_pack_summary.json", timeout=180, allow_fail=True)
    reports = data(cli_json(["reports", "list", bb], out=case_dir / "reports_list.json", timeout=180, allow_fail=True)) or []
    report_version_ids: list[str] = []
    for report in reports if isinstance(reports, list) else []:
        report_id = report.get("id")
        if not report_id:
            continue
        versions = data(cli_json(["reports", "versions", report_id], out=case_dir / f"report_versions_{report_id}.json", timeout=180, allow_fail=True)) or []
        for version in versions if isinstance(versions, list) else []:
            vid = version.get("id")
            if vid:
                report_version_ids.append(vid)
    for vid in sorted(set(report_version_ids)):
        cli(["reports", "view", vid, "--format", "json"], out=case_dir / f"report_{vid}.json", timeout=240, allow_fail=True)
        run(["worldfork", "--timeout", "240", "reports", "view", vid], out=case_dir / f"report_{vid}.md", timeout=240, allow_fail=True)
        if RENDER_REPORT_PDFS:
            pdf_path = case_dir / f"render_{vid}.pdf"
            cli(
                ["reports", "render", vid, "--format", "pdf", "--output", str(pdf_path)],
                out=case_dir / f"render_{vid}.json",
                timeout=240,
                allow_fail=True,
            )
            if pdf_path.exists() and not COPY_RENDERED_ARTIFACTS:
                pdf_path.unlink()
    max_ticks = {}
    branch_edges = 0
    inherited_ticks = 0
    for multiverse in multiverses if isinstance(multiverses, list) else []:
        mid = multiverse.get("id")
        if not mid:
            continue
        max_ticks[mid] = latest_tick_index(mid, case_dir, mid)
        lineage = data(cli_json(["query", "GET", f"/api/multiverses/{mid}/lineage"], out=case_dir / f"lineage_{mid}.json", timeout=180, allow_fail=True)) or {}
        branch_edges += len(lineage.get("edges") or [])
        inherited_ticks += len(lineage.get("inherited_ticks") or [])
    return {
        "workspace": workspace,
        "multiverse_count": len(multiverses) if isinstance(multiverses, list) else 0,
        "max_tick_by_multiverse": max_ticks,
        "target_max_ticks": TARGET_MAX_TICKS,
        "target_reached": bool(max_ticks) and max(max_ticks.values()) >= TARGET_MAX_TICKS,
        "branch_edges": branch_edges,
        "inherited_ticks": inherited_ticks,
        "ledger_version_count": len(set(ledger_version_ids)),
        "report_version_count": len(set(report_version_ids)),
        "report_evidence_pack_collected": (case_dir / "report_evidence_pack_summary.json").exists(),
        "timeline_adjudication_collected": (case_dir / "timeline_adjudication_latest.json").exists(),
    }


def run_until_complete_with_retries(bb: str, case_dir: Path, result: dict[str, Any]) -> None:
    max_attempts = RUN_UNTIL_COMPLETE_RETRIES + 1
    attempts = []
    last_error = ""
    for attempt_index in range(max_attempts):
        attempt_number = attempt_index + 1
        payload = {"max_total_ticks": 320 if attempt_index == 0 else 500}
        output_name = "run_until_complete.json" if attempt_index == 0 else f"run_until_complete_retry_{attempt_index:02d}.json"
        proc = cli(
            ["query", "POST", f"/api/big-bangs/{bb}/run-until-complete", "--data", json.dumps(payload)],
            out=case_dir / output_name,
            timeout=21600,
            allow_fail=True,
        )
        last_error = (proc.stderr or proc.stdout or "").strip()
        retryable = proc.returncode != 0 and is_retryable_run_until_complete_error(last_error)
        rate_limited = proc.returncode != 0 and is_rate_limit_error(last_error)
        if rate_limited:
            result["rate_limit_observed"] = True
        attempts.append(
            {
                "attempt": attempt_number,
                "max_attempts": max_attempts,
                "exit_code": proc.returncode,
                "max_total_ticks": payload["max_total_ticks"],
                "retryable": retryable,
                "rate_limited": rate_limited,
                "stderr_excerpt": (proc.stderr or "")[:1000],
            }
        )
        write_json(case_dir / "run_until_complete_attempts.json", attempts)
        if proc.returncode == 0:
            result["run_until_complete_attempts"] = attempts
            return

        result["errors"].append(
            f"run-until-complete attempt {attempt_number}/{max_attempts} failed: {last_error[:1000]}"
        )
        if attempt_number >= max_attempts:
            break
        if not retryable:
            result["errors"].append("run-until-complete stopped retries after a non-retryable error")
            break
        time.sleep(RUN_UNTIL_COMPLETE_RETRY_DELAY_SECONDS)

    result["run_until_complete_attempts"] = attempts
    raise RuntimeError(
        f"run-until-complete failed after {len(attempts)} attempt(s); last error: {last_error[:1000]}"
    )


def is_retryable_run_until_complete_error(message: str) -> bool:
    text = message.lower()
    retry_markers = (
        "llm unavailable",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "rate limit",
        "request timed out",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "server disconnected",
    )
    return any(marker in text for marker in retry_markers)


def is_rate_limit_error(message: str) -> bool:
    text = message.lower()
    rate_limit_markers = (
        "http 429",
        "429 too many",
        "too many requests",
        "rate limit",
        "rate-limited",
        "rate limited",
    )
    return any(marker in text for marker in rate_limit_markers)


class AdaptiveConcurrency:
    def __init__(self) -> None:
        self.limit = min(INITIAL_CONCURRENCY, MAX_CONCURRENCY)
        self.successes_since_scale = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "current_limit": self.limit,
            "initial_concurrency": INITIAL_CONCURRENCY,
            "max_concurrency": MAX_CONCURRENCY,
            "scale_after_successes": CONCURRENCY_SCALE_AFTER_SUCCESSES,
            "rate_limit_cooldown_seconds": RATE_LIMIT_COOLDOWN_SECONDS,
        }

    def observe_result(self, result: dict[str, Any]) -> None:
        if result.get("rate_limit_observed"):
            old_limit = self.limit
            self.limit = 1
            self.successes_since_scale = 0
            append_jsonl(
                RUN_DIR / "concurrency-events.jsonl",
                {
                    "timestamp": now_iso(),
                    "event": "rate_limit_reduce",
                    "case_id": result.get("case_id"),
                    "old_limit": old_limit,
                    "new_limit": self.limit,
                    "reason": "actual provider 429/rate-limit evidence overrides configured concurrency",
                },
            )
            time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
            return

        if result.get("status") == "completed":
            self.successes_since_scale += 1
            if self.limit < MAX_CONCURRENCY and self.successes_since_scale >= CONCURRENCY_SCALE_AFTER_SUCCESSES:
                old_limit = self.limit
                self.limit += 1
                self.successes_since_scale = 0
                append_jsonl(
                    RUN_DIR / "concurrency-events.jsonl",
                    {
                        "timestamp": now_iso(),
                        "event": "scale_up",
                        "case_id": result.get("case_id"),
                        "old_limit": old_limit,
                        "new_limit": self.limit,
                        "reason": f"{CONCURRENCY_SCALE_AFTER_SUCCESSES} completed case(s) without rate-limit evidence",
                    },
                )


def run_cases_adaptively(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    pending = list(cases)
    scheduler = AdaptiveConcurrency()
    running: dict[concurrent.futures.Future[dict[str, Any]], dict[str, Any]] = {}
    append_jsonl(
        RUN_DIR / "concurrency-events.jsonl",
        {"timestamp": now_iso(), "event": "start", **scheduler.snapshot()},
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        while pending or running:
            while pending and len(running) < scheduler.limit:
                case = pending.pop(0)
                running[pool.submit(continue_case, case)] = case
                append_jsonl(
                    RUN_DIR / "concurrency-events.jsonl",
                    {
                        "timestamp": now_iso(),
                        "event": "case_started",
                        "case_id": case["case_id"],
                        "active_cases": len(running),
                        "pending_cases": len(pending),
                        **scheduler.snapshot(),
                    },
                )
            if not running:
                continue

            done, _ = concurrent.futures.wait(
                running,
                timeout=5,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in done:
                case = running.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"case_id": case["case_id"], "category": case["category"], "status": "failed", "errors": [str(exc)]}
                    append_jsonl(RUN_DIR / "overnight-cases.jsonl", result)
                results.append(result)
                scheduler.observe_result(result)
                write_json(
                    RUN_DIR / "status.json",
                    {
                        "status": "running",
                        "updated_at": now_iso(),
                        "pid": os.getpid(),
                        "completed_cases": len(results),
                        "case_count": len(cases),
                        "active_cases": len(running),
                        "pending_cases": len(pending),
                        "adaptive_concurrency": scheduler.snapshot(),
                    },
                )
    return results


def continue_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = case["case_id"]
    bb = case["big_bang_id"]
    case_dir = RUN_DIR / "raw" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result: dict[str, Any] = {
        "case_id": case_id,
        "category": case["category"],
        "big_bang_id": bb,
        "root_multiverse_id": case["root_multiverse_id"],
        "started_at": now_iso(),
        "status": "running",
        "errors": [],
    }
    write_json(case_dir / "case-start.json", result)
    try:
        multiverses = data(cli_json(["query", "GET", f"/api/big-bangs/{bb}/multiverses"], out=case_dir / "multiverses-before.json", timeout=180))
        if not isinstance(multiverses, list):
            raise RuntimeError("multiverse list did not return a list")
        for multiverse in multiverses:
            mid = multiverse["id"]
            latest = latest_tick_index(mid, case_dir, f"{mid}_before")
            status = multiverse.get("status")
            if latest < TARGET_MAX_TICKS and status in {"completed", "terminated"}:
                payload = {
                    "max_ticks": TARGET_MAX_TICKS,
                    "reason": "overnight long-horizon accuracy run to 35 ticks",
                }
                cli_json(
                    ["query", "POST", f"/api/multiverses/{mid}/continue", "--data", json.dumps(payload)],
                    out=case_dir / f"continue_{mid}.json",
                    timeout=240,
                )
        run_until_complete_with_retries(bb, case_dir, result)
        collected = collect_case_artifacts(case, case_dir)
        result.update(collected)
        if result.get("target_reached"):
            result["status"] = "completed"
        else:
            result["status"] = "incomplete"
            result["errors"].append(f"no multiverse reached target tick {TARGET_MAX_TICKS}")
    except Exception as exc:
        result["status"] = "failed"
        result["errors"].append(str(exc))
        try:
            result.update(collect_case_artifacts(case, case_dir))
        except Exception as collect_exc:
            result["errors"].append(f"collection failed: {collect_exc}")
    result["finished_at"] = now_iso()
    result["duration_seconds"] = round(time.time() - started, 3)
    write_json(case_dir / "case-result.json", result)
    append_jsonl(RUN_DIR / "overnight-cases.jsonl", result)
    return result


def parse_percent(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return 0.0


def parse_bytes(value: str | None) -> float:
    if not value:
        return 0.0
    token = str(value).split("/")[0].strip()
    match = re.match(r"([0-9.]+)\s*([A-Za-z]+)?", token)
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
    }
    return number * factors.get(unit, 1)


def summarize_resources() -> dict[str, Any]:
    peak: dict[str, dict[str, float]] = defaultdict(lambda: {"cpu": 0.0, "mem_bytes": 0.0})
    stats_path = RUN_DIR / "docker-stats.jsonl"
    if stats_path.exists():
        for line in stats_path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("Name") or row.get("Container") or "unknown"
            peak[name]["cpu"] = max(peak[name]["cpu"], parse_percent(row.get("CPUPerc")))
            peak[name]["mem_bytes"] = max(peak[name]["mem_bytes"], parse_bytes(row.get("MemUsage")))
    restarts = Counter()
    oom = []
    health_failures = []
    inspect_path = RUN_DIR / "docker-inspect.jsonl"
    if inspect_path.exists():
        for line in inspect_path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("name") or "unknown"
            restarts[name] = max(restarts[name], int(row.get("restart_count") or 0))
            state = row.get("state") or {}
            if state.get("OOMKilled") or state.get("Error"):
                oom.append({"name": name, "state": state, "timestamp": row.get("timestamp")})
            health = row.get("health") or state.get("Health") or {}
            if isinstance(health, dict) and health.get("Status") not in (None, "healthy"):
                health_failures.append({"name": name, "health": health, "timestamp": row.get("timestamp")})
    docker_events_lines = 0
    events_path = RUN_DIR / "docker-events.log"
    if events_path.exists():
        docker_events_lines = sum(1 for line in events_path.read_text(errors="replace").splitlines() if line.strip())
    final_snapshots = {
        "docker_compose_ps_after": (RUN_DIR / "docker-compose-ps-after.txt").exists(),
        "docker_inspect_final": (RUN_DIR / "docker-inspect-final.json").exists(),
        "docker_system_df_after": (RUN_DIR / "docker-system-df-after.txt").exists(),
        "host_disk_after": (RUN_DIR / "host-disk-after.txt").exists(),
        "worktree_disk_after": (RUN_DIR / "worktree-disk-after.txt").exists(),
    }
    artifact_bytes = sum(path.stat().st_size for path in RUN_DIR.rglob("*") if path.is_file())
    summary = {
        "peak_by_container": {
            name: {"peak_cpu_percent": vals["cpu"], "peak_mem_mib": round(vals["mem_bytes"] / 1024 / 1024, 2)}
            for name, vals in sorted(peak.items())
        },
        "restart_counts": dict(restarts),
        "oom_or_error_events": oom,
        "health_failures": health_failures,
        "docker_stats_samples": sum(1 for line in stats_path.read_text().splitlines() if line.strip()) if stats_path.exists() else 0,
        "docker_events_lines": docker_events_lines,
        "final_snapshots": final_snapshots,
        "artifact_bytes": artifact_bytes,
        "artifact_mib": round(artifact_bytes / 1024 / 1024, 2),
    }
    write_json(RUN_DIR / "resource-summary.json", summary)
    lines = ["# Resource Summary", "", f"Generated: {now_iso()}", ""]
    for name, vals in summary["peak_by_container"].items():
        lines.append(f"- {name}: peak CPU {vals['peak_cpu_percent']:.2f}%, peak memory {vals['peak_mem_mib']:.2f} MiB, restarts {summary['restart_counts'].get(name, 0)}")
    if oom:
        lines.append("")
        lines.append("OOM/error events were observed:")
        for event in oom:
            lines.append(f"- {event['timestamp']} {event['name']}: {event['state']}")
    else:
        lines.append("")
        lines.append("No OOM/error events were observed in docker inspect telemetry.")
    if health_failures:
        lines.append("")
        lines.append("Container health failures were observed:")
        for event in health_failures:
            lines.append(f"- {event['timestamp']} {event['name']}: {event['health']}")
    lines.append("")
    lines.append(f"Docker stats samples: {summary['docker_stats_samples']}")
    lines.append(f"Docker events lines: {docker_events_lines}")
    lines.append(f"Artifact directory size: {summary['artifact_mib']} MiB")
    lines.append(f"Final snapshots present: {final_snapshots}")
    (RUN_DIR / "resource-summary.md").write_text("\n".join(lines) + "\n")
    return summary


def copy_rendered_artifacts() -> None:
    manifest = {
        "preserved": False,
        "enabled": COPY_RENDERED_ARTIFACTS,
        "limit_mib": RENDERED_COPY_LIMIT_MIB,
        "timestamp": now_iso(),
    }
    pdfs = sorted((RUN_DIR / "raw").rglob("render_*.pdf"))
    total_bytes = sum(path.stat().st_size for path in pdfs if path.exists())
    manifest["pdf_count"] = len(pdfs)
    manifest["total_mib"] = round(total_bytes / 1024 / 1024, 2)
    if not COPY_RENDERED_ARTIFACTS:
        manifest["reason"] = "WF_COPY_RENDERED_ARTIFACTS is not enabled."
        write_json(RUN_DIR / "rendered-artifacts-copy.json", manifest)
        return
    if RENDERED_COPY_LIMIT_MIB > 0 and manifest["total_mib"] > RENDERED_COPY_LIMIT_MIB:
        for path in pdfs:
            path.unlink(missing_ok=True)
        manifest["reason"] = "explicit PDF render files exceeded WF_RENDERED_COPY_LIMIT_MIB and were deleted"
        write_json(RUN_DIR / "rendered-artifacts-copy.json", manifest)
        return
    manifest["preserved"] = True
    manifest["target"] = "raw/<case_id>/render_<report_version_id>.pdf"
    write_json(RUN_DIR / "rendered-artifacts-copy.json", manifest)


def count_ledger_quality(case_dirs: list[Path]) -> dict[str, Any]:
    entries = 0
    empty_authority = 0
    empty_evidence = 0
    by_case = {}
    for case_dir in case_dirs:
        ce = ca = cv = 0
        for path in case_dir.glob("ledger_*.json"):
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            payload = data(payload)
            rows = []
            if isinstance(payload, dict):
                rows = payload.get("entries") or (payload.get("ledger") or {}).get("entries") or []
            elif isinstance(payload, list):
                rows = payload
            for entry in rows:
                if not isinstance(entry, dict):
                    continue
                entries += 1
                ce += 1
                if not entry.get("authority_refs"):
                    empty_authority += 1
                    ca += 1
                if not entry.get("evidence_refs"):
                    empty_evidence += 1
                    cv += 1
        by_case[case_dir.name] = {"entries": ce, "empty_authority_refs": ca, "empty_evidence_refs": cv}
    return {
        "entries": entries,
        "empty_authority_refs": empty_authority,
        "empty_evidence_refs": empty_evidence,
        "by_case": by_case,
    }


def count_adjudication_quality(case_dirs: list[Path]) -> dict[str, Any]:
    versions = 0
    entries = 0
    retained = 0
    pruned = 0
    excluded_mass = 0.0
    by_status: Counter[str] = Counter()
    for case_dir in case_dirs:
        path = case_dir / "timeline_adjudication_latest.json"
        if not path.exists():
            continue
        try:
            payload = data(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        versions += 1
        rows = payload.get("entries") or []
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            entries += 1
            if entry.get("include_in_final"):
                retained += 1
            else:
                pruned += 1
                try:
                    excluded_mass += float(entry.get("original_path_probability") or 0.0)
                except (TypeError, ValueError):
                    pass
            by_status[str(entry.get("viability_status") or "unknown")] += 1
    return {
        "versions": versions,
        "entries": entries,
        "retained": retained,
        "pruned": pruned,
        "excluded_original_path_probability_mass": round(excluded_mass, 10),
        "viability_statuses": dict(by_status),
    }


def model_counts(case_dirs: list[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for case_dir in case_dirs:
        path = case_dir / "llm_logs.json"
        if not path.exists():
            continue
        try:
            rows = data(json.loads(path.read_text())) or []
        except json.JSONDecodeError:
            continue
        for row in rows if isinstance(rows, list) else []:
            counts[str(row.get("model"))] += 1
    return counts


def write_improvement_report(results: list[dict[str, Any]], resource_summary: dict[str, Any]) -> None:
    case_dirs = [RUN_DIR / "raw" / row["case_id"] for row in results]
    ledger = count_ledger_quality(case_dirs)
    adjudication = count_adjudication_quality(case_dirs)
    models = model_counts(case_dirs)
    completed = [row for row in results if row.get("status") == "completed"]
    failed = [row for row in results if row.get("status") != "completed"]
    max_ticks = []
    branch_edges = 0
    inherited_ticks = 0
    ledger_versions = 0
    reports = 0
    for row in results:
        max_ticks.extend((row.get("max_tick_by_multiverse") or {}).values())
        branch_edges += int(row.get("branch_edges") or 0)
        inherited_ticks += int(row.get("inherited_ticks") or 0)
        ledger_versions += int(row.get("ledger_version_count") or 0)
        reports += int(row.get("report_version_count") or 0)
    min_tick = min(max_ticks) if max_ticks else None
    max_tick = max(max_ticks) if max_ticks else None
    pdf_count = len(list((RUN_DIR / "raw").rglob("render_*.pdf")))
    recommendations = [
        "Add endpoint-ledger quality gates: every endpoint should carry authority_refs and evidence_refs, and reports should downgrade unsupported endpoints.",
        "Separate completed evidence from queued future events in report prompts and report schemas so cheap models do not overinterpret future scheduled events.",
        "Use branch-sensitive benchmark cells with thresholds 0.55, 0.75, and 0.95; require at least one admitted branch and one rejected branch candidate in the benchmark.",
        "Give the God agent a compact hard-rules checklist before tool selection, especially around actor authority and illegal/impossible direct actions.",
        "Prune report input aggressively: include compact tick deltas, endpoint ledger diffs, top cohort state changes, and God decisions instead of full repeated bundles.",
        "Use cheap-model redundancy for high-risk steps: one generation plus a cheap verifier for endpoint authority, prompt injection, and report contradiction checks.",
        "Persist typed risk_flags for prompt injection and low-information scenarios; do not rely on narrative risk_notes alone.",
        "Score accuracy with artifact-cited LLM judges plus deterministic validators. Structural word-count/token-overlap scoring should be a smoke check only.",
        "Add resource gates to CI-style long runs: fail or mark inconclusive on container OOM, repeated restarts, severe disk pressure, or missing Docker telemetry.",
        "Use cohort-state deltas in reports: attention, trust, fatigue, mobilization, silence, dependency, and conflict should be summarized by tick window.",
    ]
    report = [
        "# Overnight 35-Tick Accuracy Improvement Report",
        "",
        f"Generated: {now_iso()}",
        f"Run directory: `{RUN_DIR}`",
        f"Backend: `{BASE_URL}`",
        f"Compose project: `{PROJECT}`",
        f"Initial concurrency: `{INITIAL_CONCURRENCY}`",
        f"Max concurrency: `{MAX_CONCURRENCY}`",
        f"Models: `{MODEL}`",
        "",
        "## Executive Summary",
        "",
        f"- Cases attempted: {len(results)}",
        f"- Cases completed with at least one multiverse at tick {TARGET_MAX_TICKS}: {len(completed)}",
        f"- Cases failed/incomplete by the 35-tick gate: {len(failed)}",
        f"- Tick range observed: {min_tick} to {max_tick}",
        f"- Branch lineage edges observed: {branch_edges}",
        f"- Inherited tick refs observed: {inherited_ticks}",
        f"- Endpoint ledger versions collected: {ledger_versions}",
        f"- Report versions collected: {reports}",
        f"- PDF output files preserved, if explicitly requested: {pdf_count}",
        f"- Endpoint ledger entries: {ledger['entries']}",
        f"- Empty authority refs: {ledger['empty_authority_refs']}",
        f"- Empty evidence refs: {ledger['empty_evidence_refs']}",
        f"- Timeline adjudication versions collected: {adjudication['versions']}",
        f"- Timeline adjudication entries retained/pruned: {adjudication['retained']}/{adjudication['pruned']}",
        f"- Timeline adjudication excluded original path mass: {adjudication['excluded_original_path_probability_mass']}",
        f"- LLM model counts: {dict(models)}",
        "",
        "## What This Says About Accuracy",
        "",
        "The strongest signal is whether long-horizon runs keep causal state coherent while producing grounded reports. The biggest known risk from the earlier 2-tick sweep was that reports looked polished while branch behavior, endpoint authority, and future-event handling were under-tested. This run extends the same cases toward 35 ticks and records the resource profile so accuracy issues can be separated from runtime pressure.",
        "",
        "## Main Improvement Recommendations",
        "",
    ]
    report.extend(f"{idx}. {item}" for idx, item in enumerate(recommendations, start=1))
    report.extend(
        [
            "",
            "## Resource Summary",
            "",
        ]
    )
    for name, vals in (resource_summary.get("peak_by_container") or {}).items():
        report.append(f"- {name}: peak CPU {vals['peak_cpu_percent']:.2f}%, peak memory {vals['peak_mem_mib']:.2f} MiB")
    if resource_summary.get("oom_or_error_events"):
        report.append("- OOM/error events were observed; inspect `resource-summary.json`.")
    else:
        report.append("- No OOM/error events were observed in captured inspect telemetry.")
    if failed:
        report.extend(["", "## Failed Or Incomplete Cases", ""])
        for row in failed:
            report.append(f"- {row['case_id']}: {row.get('errors')}")
    artifact_index = [
        "",
        "## Artifact Index",
        "",
        "- `overnight-cases.jsonl`",
        "- `command-results.jsonl`",
        "- `docker-stats.jsonl`",
        "- `docker-events.log`",
        "- `resource-summary.md`",
        "- `raw/<case_id>/`",
    ]
    if RENDER_REPORT_PDFS and COPY_RENDERED_ARTIFACTS:
        artifact_index.append("- `raw/<case_id>/render_*.pdf` (explicit PDF render output)")
    report.extend(artifact_index)
    (RUN_DIR / "accuracy-improvement-report.md").write_text("\n".join(report) + "\n")
    write_json(
        RUN_DIR / "overnight-summary.json",
        {
            "results": results,
            "ledger_quality": ledger,
            "timeline_adjudication_quality": adjudication,
            "model_counts": dict(models),
            "resource_summary": resource_summary,
            "pdf_count": pdf_count,
            "completed_count": len(completed),
            "failed_count": len(failed),
            "min_tick": min_tick,
            "max_tick": max_tick,
            "branch_edges": branch_edges,
            "inherited_ticks": inherited_ticks,
            "ledger_versions": ledger_versions,
            "report_versions": reports,
            "recommendations": recommendations,
        },
    )


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    write_json(RUN_DIR / "status.json", {"status": "starting", "started_at": now_iso(), "pid": os.getpid()})
    preflight()
    start_stack()
    record_before()
    monitor_thread, events_proc = start_monitors()
    monitors_stopped = False
    results: list[dict[str, Any]] = []
    try:
        cases = load_cases()
        write_json(RUN_DIR / "case-plan.json", cases)
        write_json(
            RUN_DIR / "status.json",
            {
                "status": "running",
                "started_at": now_iso(),
                "pid": os.getpid(),
                "case_count": len(cases),
                "adaptive_concurrency": AdaptiveConcurrency().snapshot(),
            },
        )
        results = run_cases_adaptively(cases)
        copy_rendered_artifacts()
        stop_monitors(monitor_thread, events_proc)
        monitors_stopped = True
        resource_summary = summarize_resources()
        write_improvement_report(results, resource_summary)
        write_json(RUN_DIR / "status.json", {"status": "completed", "finished_at": now_iso(), "pid": os.getpid(), "completed_cases": len(results)})
        return 0
    except Exception as exc:
        write_json(RUN_DIR / "status.json", {"status": "failed", "failed_at": now_iso(), "pid": os.getpid(), "error": str(exc), "completed_cases": len(results)})
        raise
    finally:
        if not monitors_stopped:
            stop_monitors(monitor_thread, events_proc)


if __name__ == "__main__":
    raise SystemExit(main())
