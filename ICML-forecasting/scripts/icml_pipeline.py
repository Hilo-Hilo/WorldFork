#!/usr/bin/env python3
"""Utilities for the WorldFork ICML forecasting paper package.

This script intentionally keeps private evaluation data out of generated case
files. Forecast-producing systems should consume only the files produced by
``prepare-cases`` or the public JSONL cards.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PACKAGE = ROOT / "ICML-forecasting"
EXISTING_72 = ROOT / "skills/worldfork-full-agent-test/references/accuracy-benchmark-prompts.jsonl"
PUBLIC_36 = PACKAGE / "worldfork_additional_36_public.jsonl"
PRIVATE_36 = PACKAGE / "worldfork_additional_36_private_eval.jsonl"
LEGACY_36 = PACKAGE / "worldfork_additional_36_legacy_schema.jsonl"
RUN_MATRIX = PACKAGE / "AGENT_BENCHMARK_RUN_MATRIX.json"
NO_BRANCH_POLICY = {
    "max_branch_depth": 1,
    "max_active_multiverses": 1,
    "max_branches_per_tick": 1,
    "branch_score_threshold": 0.999,
}
SHORT_BRANCH_POLICY = {
    "max_branch_depth": 2,
    "max_active_multiverses": 4,
    "max_branches_per_tick": 1,
    "branch_score_threshold": 0.75,
}
WORLDFORK_SHORT_POLICIES = {
    "worldfork_no_branch_short": NO_BRANCH_POLICY,
    "worldfork_branching_short": SHORT_BRANCH_POLICY,
}
INIT_ARTIFACT_NAMES = [
    "initialization",
    "actors",
    "traits",
    "graphs",
    "sociology_baseline",
    "emotion_baseline",
    "llm_logs",
    "workspace",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_run_root(base: Path | None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_root = base or ROOT / "paper_runs" / f"worldfork_icml_{timestamp}"
    for child in [
        "setup",
        "cases/existing_72",
        "cases/additional_36",
        "manifests",
        "raw",
        "results",
        "paper/tables",
        "paper/figures",
    ]:
        (run_root / child).mkdir(parents=True, exist_ok=True)
    return run_root


def resolve_case_file(run_root: Path, case_id: str) -> Path:
    for relative in [
        Path("cases/additional_36") / f"{case_id}.md",
        Path("cases/existing_72") / f"{case_id}.md",
    ]:
        path = run_root / relative
        if path.exists():
            return path
    raise FileNotFoundError(f"case file not found for {case_id}")


def build_init_job_payload(
    *,
    case_id: str,
    case_file: Path,
    name_prefix: str,
    max_ticks: int,
    tick_duration_minutes: int,
    branch_policy: dict[str, Any],
) -> dict[str, Any]:
    return {
        "job_type": "initialize_big_bang",
        "payload": {
            "name": f"{name_prefix}_{case_id}",
            "scenario_text": case_file.read_text(encoding="utf-8"),
            "simulation_config": {
                "max_ticks": max_ticks,
                "tick_duration_minutes": tick_duration_minutes,
            },
            "branch_policy": branch_policy,
            "actors": [],
            "cohorts": [],
            "heroes": [],
            "use_initializer_agent": True,
        },
    }


def init_manifest_row(
    *,
    case_id: str,
    condition: str,
    big_bang_id: str,
    job_id: str,
    status: str,
    wait_seconds: float,
    run_dir: Path,
    actor_count: int,
    trait_count: int,
    llm_log_count: int,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "condition": condition,
        "big_bang_id": big_bang_id,
        "job_id": job_id,
        "status": status,
        "job_wait_wall_time_seconds": wait_seconds,
        "run_dir": str(run_dir),
        "notes": f"Queued initializer batch member; actors={actor_count}, traits={trait_count}, llm_logs={llm_log_count}.",
    }


class ApiClient:
    def __init__(self, base_url: str, api_prefix: str = "/api", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.api_prefix = api_prefix.strip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        relative = path.lstrip("/")
        if self.api_prefix and not relative.startswith(f"{self.api_prefix}/"):
            relative = f"{self.api_prefix}/{relative}"
        url = urljoin(self.base_url, relative)
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            body = response.read()
        if not body:
            return None
        return json.loads(body.decode("utf-8"))


def _timed_api_call(client: ApiClient, method: str, path: str, *, payload: dict[str, Any] | None = None) -> tuple[Any, float]:
    started = datetime.now(UTC)
    result = client.request(method, path, payload=payload)
    elapsed = (datetime.now(UTC) - started).total_seconds()
    return result, elapsed


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _count_payload(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return len(data)
    return 0


def init_artifacts_complete(out_dir: Path) -> bool:
    return all((out_dir / f"{name}.json").exists() for name in INIT_ARTIFACT_NAMES)


def _case_ids_from_manifest(run_root: Path, case_ids: str | None, case_limit: int | None) -> list[str]:
    if case_ids:
        ids = [item.strip() for item in case_ids.split(",") if item.strip()]
    else:
        manifest = run_root / "manifests/benchmark_case_manifest.jsonl"
        ids = [row["case_id"] for row in read_jsonl(manifest)]
    if case_limit:
        ids = ids[:case_limit]
    return ids


def _job_finished(job: dict[str, Any]) -> bool:
    return str(job.get("status")) in {"succeeded", "failed", "cancelled", "interrupted", "interrupt_requested"}


def _wait_for_job(
    client: ApiClient,
    job_id: str,
    *,
    out_dir: Path,
    artifact_prefix: str = "job",
    wait_timeout: float,
    poll_seconds: float,
    submitted_at: float,
) -> tuple[dict[str, Any], float]:
    deadline = time.monotonic() + wait_timeout
    while True:
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for job {job_id}")
        job = client.request("GET", f"/jobs/{job_id}")
        _write_json(out_dir / f"{artifact_prefix}_status_latest.json", job)
        if _job_finished(job):
            wait_seconds = time.monotonic() - submitted_at
            _write_json(out_dir / f"{artifact_prefix}_wait.json", {"ok": True, "data": job, "meta": {"terminal": True}})
            (out_dir / f"{artifact_prefix}_wait_time_and_stderr.txt").write_text(f"real {wait_seconds:.2f}\n", encoding="utf-8")
            return job, wait_seconds
        time.sleep(poll_seconds)


def _capture_init_artifacts(client: ApiClient, out_dir: Path, big_bang_id: str) -> dict[str, int]:
    captures = {
        "initialization": f"/big-bangs/{big_bang_id}/initialization",
        "actors": f"/big-bangs/{big_bang_id}/initialization/actors",
        "traits": f"/big-bangs/{big_bang_id}/initialization/traits",
        "graphs": f"/big-bangs/{big_bang_id}/initialization/graphs",
        "sociology_baseline": f"/big-bangs/{big_bang_id}/initialization/sociology-baseline",
        "emotion_baseline": f"/big-bangs/{big_bang_id}/initialization/emotion-baseline",
        "llm_logs": f"/agent/logs?run_id={big_bang_id}&source=llm&verbosity=normal",
        "workspace": f"/agent/runs/{big_bang_id}/workspace?verbosity=summary",
    }
    counts: dict[str, int] = {}
    for name, path in captures.items():
        try:
            payload = client.request("GET", path)
        except urllib.error.HTTPError as exc:
            payload = {
                "ok": False,
                "error": {
                    "type": "http_error",
                    "status": exc.code,
                    "reason": exc.reason,
                    "path": path,
                },
            }
        _write_json(out_dir / f"{name}.json", payload)
        counts[name] = _count_payload(payload)
    return counts


def _evaluate_big_bang_endpoint_ledger(client: ApiClient, out_dir: Path, big_bang_id: str) -> None:
    try:
        payload = client.request(
            "POST",
            f"/big-bangs/{big_bang_id}/endpoint-ledgers/evaluate",
            payload={"run_inline": True},
        )
    except urllib.error.HTTPError as exc:
        payload = {"ok": False, "error": {"type": "http_error", "status": exc.code, "reason": exc.reason}}
    _write_json(out_dir / "endpoint_ledger_evaluate.json", payload)


def _capture_run_artifacts(client: ApiClient, out_dir: Path, big_bang_id: str) -> None:
    _evaluate_big_bang_endpoint_ledger(client, out_dir, big_bang_id)
    captures = {
        "timing": f"/agent/runs/{big_bang_id}/timing",
        "cost": f"/agent/runs/{big_bang_id}/cost?include_calls=true&include_non_openrouter=true",
        "reports_list": f"/big-bangs/{big_bang_id}/reports",
        "ledgers_list": f"/big-bangs/{big_bang_id}/endpoint-ledgers",
        "path_mass": f"/big-bangs/{big_bang_id}/endpoint-ledgers/path-mass",
        "workspace": f"/agent/runs/{big_bang_id}/workspace?verbosity=summary",
        "llm_logs_after_job": f"/agent/logs?run_id={big_bang_id}&source=llm&verbosity=normal&limit=500",
    }
    for name, path in captures.items():
        try:
            payload = client.request("GET", path)
        except urllib.error.HTTPError as exc:
            payload = {"ok": False, "error": {"type": "http_error", "status": exc.code, "reason": exc.reason, "path": path}}
        _write_json(out_dir / f"{name}.json", payload)


def _prediction_output_path(run_root: Path, value: str | None) -> Path:
    relative = Path(value or "raw/E3_worldfork_short/worldfork_predictions.jsonl")
    return relative if relative.is_absolute() else run_root / relative


def _display_run_path(path: Path, run_root: Path) -> str:
    try:
        return str(path.relative_to(run_root))
    except ValueError:
        return str(path)


def _prediction_key(row: dict[str, Any], route_policy_id: str | None = None) -> tuple[str, str, str]:
    policy = route_policy_id if route_policy_id is not None else str(row.get("route_policy_id") or "")
    return (str(row.get("case_id")), str(row.get("condition")), policy)


def _annotate_prediction(prediction: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    route_policy_id = getattr(args, "route_policy_id", None)
    if route_policy_id:
        prediction["route_policy_id"] = route_policy_id
    prediction["max_ticks_requested"] = int(getattr(args, "max_ticks", 0) or 0)
    prediction["tick_duration_minutes"] = int(getattr(args, "tick_duration_minutes", 0) or 0)
    return prediction


def refresh_worldfork_short_ledgers(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    manifest_path = run_root / "manifests/worldfork_short_manifest.jsonl"
    output = _prediction_output_path(run_root, args.prediction_output)
    case_filter = {item.strip() for item in args.case_ids.split(",") if item.strip()} if args.case_ids else None
    condition_filter = {item.strip() for item in args.conditions.split(",") if item.strip()} if args.conditions else None
    route_policy_filter = {item.strip() for item in args.route_policy_ids.split(",") if item.strip()} if args.route_policy_ids else None
    existing_order: list[tuple[str, str, str]] = []
    predictions: dict[tuple[str, str, str], dict[str, Any]] = {}
    if output.exists():
        for row in read_jsonl(output):
            key = _prediction_key(row)
            if key not in predictions:
                existing_order.append(key)
            predictions[key] = row

    refreshed: list[tuple[str, str, str]] = []
    for row in read_jsonl(manifest_path):
        case_id = str(row.get("case_id") or "")
        condition = str(row.get("condition") or "")
        if row.get("status") != "completed":
            continue
        if case_filter and case_id not in case_filter:
            continue
        if condition_filter and condition not in condition_filter:
            continue
        route_policy_id = str(row.get("route_policy_id") or "")
        if route_policy_filter and route_policy_id not in route_policy_filter:
            continue
        big_bang_id = str(row.get("big_bang_id") or "")
        run_dir = run_root / str(row.get("run_dir") or "")
        if not big_bang_id or not run_dir.exists():
            continue
        _capture_run_artifacts(client, run_dir, big_bang_id)
        path_mass = json.loads((run_dir / "path_mass.json").read_text(encoding="utf-8"))
        key = (case_id, condition, route_policy_id)
        if key not in predictions:
            existing_order.append(key)
        predictions[key] = {
            **extract_worldfork_forecast(case_id, condition, path_mass),
            "route_policy_id": route_policy_id,
            "max_ticks_requested": row.get("max_ticks_requested"),
            "tick_duration_minutes": row.get("tick_duration_minutes"),
        }
        refreshed.append(key)
        print(json.dumps({"case_id": case_id, "condition": condition, "status": "refreshed"}))

    if refreshed:
        write_jsonl(output, [predictions[key] for key in existing_order if key in predictions])


def _endpoint_matches(entry: dict[str, Any], target: str) -> bool:
    target = target.lower()
    key = str(entry.get("endpoint_key") or entry.get("id") or "").lower()
    label = str(entry.get("label") or entry.get("description") or "").lower()
    if key in {target, f"{target}_endpoint"}:
        return True
    if key.endswith(f"_{target}") or key.startswith(f"{target}_"):
        return True
    if target == "yes":
        return "yes" in key or "event occurs" in label or "delivers" in label or "lowers" in label
    if target == "no":
        return "no" in key or "does not" in label or "no " in label or "not " in label
    return False


def extract_worldfork_forecast(case_id: str, condition: str, path_mass_payload: dict[str, Any]) -> dict[str, Any]:
    rows = path_mass_payload.get("endpoint_path_mass_distribution") or []
    if not isinstance(rows, list):
        rows = []
    yes_mass = 0.0
    no_mass = 0.0
    unresolved_mass = 0.0
    matched_rows = 0
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        mass = float(entry.get("path_mass") or 0.0)
        status_masses = entry.get("status_path_masses") if isinstance(entry.get("status_path_masses"), dict) else {}
        unresolved_mass += float(status_masses.get("unresolved") or 0.0)
        unresolved_mass += float(status_masses.get("insufficient_ticks") or 0.0)
        if _endpoint_matches(entry, "yes"):
            yes_mass += mass
            matched_rows += 1
        elif _endpoint_matches(entry, "no"):
            no_mass += mass
            matched_rows += 1
    denom = yes_mass + no_mass
    if denom > 0:
        p_yes = yes_mass / denom
        p_no = no_mass / denom
    else:
        p_yes = 0.5
        p_no = 0.5
    unresolved = min(1.0, unresolved_mass / max(1, matched_rows)) if matched_rows else 1.0
    return {
        "case_id": case_id,
        "condition": condition,
        "p_yes": p_yes,
        "p_no": p_no,
        "unresolved_mass": unresolved,
        "forecast_distribution": {"yes": p_yes, "no": p_no, "unresolved": unresolved},
        "extraction_note": "derived_from_endpoint_path_mass_distribution",
        "matched_endpoint_rows": matched_rows,
    }


def worldfork_short_manifest_row(
    *,
    case_id: str,
    condition: str,
    big_bang_id: str,
    init_job_id: str,
    run_job_id: str,
    status: str,
    init_wait_seconds: float,
    run_wait_seconds: float,
    run_dir: Path,
    ticks_run: int,
    multiverse_count: int,
    final_report_version_id: str | None,
    max_ticks_requested: int | None = None,
    tick_duration_minutes: int | None = None,
    route_policy_id: str | None = None,
    prediction_output: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "condition": condition,
        "route_policy_id": route_policy_id,
        "max_ticks_requested": max_ticks_requested,
        "tick_duration_minutes": tick_duration_minutes,
        "big_bang_id": big_bang_id,
        "init_job_id": init_job_id,
        "run_job_id": run_job_id,
        "status": status,
        "init_wait_wall_time_seconds": init_wait_seconds,
        "run_wait_wall_time_seconds": run_wait_seconds,
        "ticks_run": ticks_run,
        "multiverse_count": multiverse_count,
        "final_report_version_id": final_report_version_id,
        "run_dir": str(run_dir),
        "prediction_output": prediction_output,
    }


def run_worldfork_short(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    matrix = json.loads(RUN_MATRIX.read_text(encoding="utf-8"))
    default_ids = matrix["case_groups"]["worldfork_resolved_core12_fallback" if args.core12 else "resolved_24"]
    case_ids = _case_ids_from_manifest(run_root, args.case_ids or ",".join(default_ids), args.case_limit)
    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    output = _prediction_output_path(run_root, args.prediction_output)
    manifest = run_root / "manifests/worldfork_short_manifest.jsonl"
    completed = set()
    if output.exists() and not args.force:
        for row in read_jsonl(output):
            completed.add(_prediction_key(row, args.route_policy_id))

    for case_id in case_ids:
        for condition in conditions:
            if condition not in WORLDFORK_SHORT_POLICIES:
                raise SystemExit(f"unknown E3 condition: {condition}")
            if (case_id, condition, args.route_policy_id or "") in completed and not args.force:
                print(json.dumps({"case_id": case_id, "condition": condition, "status": "skipped_existing"}))
                continue
            relative_dir = Path(args.output_prefix) / condition / case_id
            out_dir = run_root / relative_dir
            case_file = resolve_case_file(run_root, case_id)
            payload = build_init_job_payload(
                case_id=case_id,
                case_file=case_file,
                name_prefix=f"{args.name_prefix}_{condition}",
                max_ticks=args.max_ticks,
                tick_duration_minutes=args.tick_duration_minutes,
                branch_policy=WORLDFORK_SHORT_POLICIES[condition],
            )
            _write_json(out_dir / "init_job_payload.json", payload)
            init_job, init_create_seconds = _timed_api_call(client, "POST", "/jobs", payload=payload)
            _write_json(out_dir / "init_job_create.json", init_job)
            (out_dir / "init_job_create_time_and_stderr.txt").write_text(f"real {init_create_seconds:.2f}\n", encoding="utf-8")
            init_job_id = str(init_job.get("id"))
            (out_dir / "init_job_id.txt").write_text(init_job_id + "\n", encoding="utf-8")
            print(json.dumps({"case_id": case_id, "condition": condition, "init_job_id": init_job_id, "status": "init_submitted"}))
            init_result, init_wait = _wait_for_job(
                client,
                init_job_id,
                out_dir=out_dir,
                artifact_prefix="init_job",
                wait_timeout=args.wait_timeout,
                poll_seconds=args.poll_seconds,
                submitted_at=time.monotonic(),
            )
            if init_result.get("status") != "succeeded":
                raise SystemExit(f"{case_id}/{condition}: init job ended {init_result.get('status')}")
            big_bang_id = str((init_result.get("result") or {}).get("big_bang_id"))
            (out_dir / "big_bang_id.txt").write_text(big_bang_id + "\n", encoding="utf-8")
            _capture_init_artifacts(client, out_dir, big_bang_id)

            run_payload = {"max_total_ticks": args.max_ticks}
            _write_json(out_dir / "run_job_payload.json", run_payload)
            run_job, run_create_seconds = _timed_api_call(
                client,
                "POST",
                f"/big-bangs/{big_bang_id}/run-until-complete/jobs",
                payload=run_payload,
            )
            _write_json(out_dir / "run_job_create.json", run_job)
            (out_dir / "run_job_create_time_and_stderr.txt").write_text(f"real {run_create_seconds:.2f}\n", encoding="utf-8")
            run_job_id = str(run_job.get("id"))
            (out_dir / "run_job_id.txt").write_text(run_job_id + "\n", encoding="utf-8")
            print(json.dumps({"case_id": case_id, "condition": condition, "run_job_id": run_job_id, "status": "run_submitted"}))
            run_result, run_wait = _wait_for_job(
                client,
                run_job_id,
                out_dir=out_dir,
                artifact_prefix="run_job",
                wait_timeout=args.wait_timeout,
                poll_seconds=args.poll_seconds,
                submitted_at=time.monotonic(),
            )
            if run_result.get("status") != "succeeded":
                raise SystemExit(f"{case_id}/{condition}: run job ended {run_result.get('status')}")
            _capture_run_artifacts(client, out_dir, big_bang_id)
            path_mass = json.loads((out_dir / "path_mass.json").read_text(encoding="utf-8"))
            prediction = _annotate_prediction(extract_worldfork_forecast(case_id, condition, path_mass), args)
            append_jsonl(output, prediction)
            result_payload = run_result.get("result") or {}
            append_jsonl(
                manifest,
                worldfork_short_manifest_row(
                    case_id=case_id,
                    condition=condition,
                    big_bang_id=big_bang_id,
                    init_job_id=init_job_id,
                    run_job_id=run_job_id,
                    status="completed",
                    init_wait_seconds=init_wait,
                    run_wait_seconds=run_wait,
                    run_dir=relative_dir,
                    ticks_run=int(result_payload.get("ticks_run") or 0),
                    multiverse_count=int(result_payload.get("multiverse_count") or 0),
                    final_report_version_id=result_payload.get("final_report_version_id"),
                    max_ticks_requested=args.max_ticks,
                    tick_duration_minutes=args.tick_duration_minutes,
                    route_policy_id=args.route_policy_id,
                    prediction_output=_display_run_path(output, run_root),
                ),
            )
            print(json.dumps({"case_id": case_id, "condition": condition, "status": "completed", "ticks_run": result_payload.get("ticks_run")}))


def _worldfork_short_targets(args: argparse.Namespace, run_root: Path) -> list[dict[str, Any]]:
    matrix = json.loads(RUN_MATRIX.read_text(encoding="utf-8"))
    default_ids = matrix["case_groups"]["worldfork_resolved_core12_fallback" if args.core12 else "resolved_24"]
    case_ids = _case_ids_from_manifest(run_root, args.case_ids or ",".join(default_ids), args.case_limit)
    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    output = _prediction_output_path(run_root, args.prediction_output)
    completed = set()
    if output.exists() and not args.force:
        for row in read_jsonl(output):
            completed.add(_prediction_key(row, args.route_policy_id))
    targets = []
    for case_id in case_ids:
        for condition in conditions:
            if condition not in WORLDFORK_SHORT_POLICIES:
                raise SystemExit(f"unknown E3 condition: {condition}")
            if (case_id, condition, args.route_policy_id or "") in completed and not args.force:
                print(json.dumps({"case_id": case_id, "condition": condition, "status": "skipped_existing"}))
                continue
            relative_dir = Path(args.output_prefix) / condition / case_id
            targets.append(
                {
                    "case_id": case_id,
                    "condition": condition,
                    "relative_dir": relative_dir,
                    "out_dir": run_root / relative_dir,
                }
            )
    return targets


def _wait_many_jobs(
    client: ApiClient,
    pending: dict[str, dict[str, Any]],
    *,
    artifact_prefix: str,
    wait_timeout: float,
    poll_seconds: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + wait_timeout
    completed: list[dict[str, Any]] = []
    while pending:
        if time.monotonic() > deadline:
            labels = ", ".join(sorted(pending))
            raise SystemExit(f"timed out waiting for {artifact_prefix} jobs: {labels}")
        for label, info in list(pending.items()):
            job = client.request("GET", f"/jobs/{info['job_id']}")
            _write_json(info["out_dir"] / f"{artifact_prefix}_status_latest.json", job)
            if not _job_finished(job):
                continue
            wait_seconds = time.monotonic() - float(info["submitted_at"])
            _write_json(info["out_dir"] / f"{artifact_prefix}_wait.json", {"ok": True, "data": job, "meta": {"terminal": True}})
            (info["out_dir"] / f"{artifact_prefix}_wait_time_and_stderr.txt").write_text(
                f"real {wait_seconds:.2f}\n",
                encoding="utf-8",
            )
            info["job"] = job
            info["wait_seconds"] = wait_seconds
            completed.append(info)
            pending.pop(label)
            print(
                json.dumps(
                    {
                        "case_id": info["case_id"],
                        "condition": info["condition"],
                        "job_id": info["job_id"],
                        "job_phase": artifact_prefix,
                        "status": job.get("status"),
                    }
                )
            )
        if pending:
            time.sleep(poll_seconds)
    return completed


def run_worldfork_short_batch(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    targets = _worldfork_short_targets(args, run_root)
    if not targets:
        return

    init_pending: dict[str, dict[str, Any]] = {}
    for target in targets:
        case_id = target["case_id"]
        condition = target["condition"]
        out_dir = target["out_dir"]
        case_file = resolve_case_file(run_root, case_id)
        payload = build_init_job_payload(
            case_id=case_id,
            case_file=case_file,
            name_prefix=f"{args.name_prefix}_{condition}",
            max_ticks=args.max_ticks,
            tick_duration_minutes=args.tick_duration_minutes,
            branch_policy=WORLDFORK_SHORT_POLICIES[condition],
        )
        _write_json(out_dir / "init_job_payload.json", payload)
        init_job, init_create_seconds = _timed_api_call(client, "POST", "/jobs", payload=payload)
        _write_json(out_dir / "init_job_create.json", init_job)
        (out_dir / "init_job_create_time_and_stderr.txt").write_text(f"real {init_create_seconds:.2f}\n", encoding="utf-8")
        init_job_id = str(init_job.get("id"))
        (out_dir / "init_job_id.txt").write_text(init_job_id + "\n", encoding="utf-8")
        label = f"{condition}/{case_id}"
        init_pending[label] = {
            **target,
            "job_id": init_job_id,
            "submitted_at": time.monotonic(),
        }
        print(json.dumps({"case_id": case_id, "condition": condition, "init_job_id": init_job_id, "status": "init_submitted"}))

    _write_json(run_root / "setup/worldfork_short_batch_queues_after_init_submit.json", client.request("GET", "/jobs/queues"))
    init_completed = _wait_many_jobs(
        client,
        init_pending,
        artifact_prefix="init_job",
        wait_timeout=args.wait_timeout,
        poll_seconds=args.poll_seconds,
    )

    run_pending: dict[str, dict[str, Any]] = {}
    for info in init_completed:
        job = info["job"]
        case_id = info["case_id"]
        condition = info["condition"]
        out_dir = info["out_dir"]
        if job.get("status") != "succeeded":
            append_jsonl(
                run_root / "manifests/worldfork_short_manifest.jsonl",
                worldfork_short_manifest_row(
                    case_id=case_id,
                    condition=condition,
                    big_bang_id="",
                    init_job_id=info["job_id"],
                    run_job_id="",
                    status=str(job.get("status")),
                    init_wait_seconds=float(info["wait_seconds"]),
                    run_wait_seconds=0.0,
                    run_dir=info["relative_dir"],
                    ticks_run=0,
                    multiverse_count=0,
                    final_report_version_id=None,
                    max_ticks_requested=args.max_ticks,
                    tick_duration_minutes=args.tick_duration_minutes,
                    route_policy_id=args.route_policy_id,
                    prediction_output=_display_run_path(_prediction_output_path(run_root, args.prediction_output), run_root),
                ),
            )
            continue
        big_bang_id = str((job.get("result") or {}).get("big_bang_id"))
        (out_dir / "big_bang_id.txt").write_text(big_bang_id + "\n", encoding="utf-8")
        _capture_init_artifacts(client, out_dir, big_bang_id)

        run_payload = {"max_total_ticks": args.max_ticks}
        _write_json(out_dir / "run_job_payload.json", run_payload)
        run_job, run_create_seconds = _timed_api_call(
            client,
            "POST",
            f"/big-bangs/{big_bang_id}/run-until-complete/jobs",
            payload=run_payload,
        )
        _write_json(out_dir / "run_job_create.json", run_job)
        (out_dir / "run_job_create_time_and_stderr.txt").write_text(f"real {run_create_seconds:.2f}\n", encoding="utf-8")
        run_job_id = str(run_job.get("id"))
        (out_dir / "run_job_id.txt").write_text(run_job_id + "\n", encoding="utf-8")
        label = f"{condition}/{case_id}"
        run_pending[label] = {
            **info,
            "big_bang_id": big_bang_id,
            "init_job_id": info["job_id"],
            "init_wait_seconds": float(info["wait_seconds"]),
            "job_id": run_job_id,
            "submitted_at": time.monotonic(),
        }
        print(json.dumps({"case_id": case_id, "condition": condition, "run_job_id": run_job_id, "status": "run_submitted"}))

    if not run_pending:
        return
    _write_json(run_root / "setup/worldfork_short_batch_queues_after_run_submit.json", client.request("GET", "/jobs/queues"))
    run_completed = _wait_many_jobs(
        client,
        run_pending,
        artifact_prefix="run_job",
        wait_timeout=args.wait_timeout,
        poll_seconds=args.poll_seconds,
    )

    output = _prediction_output_path(run_root, args.prediction_output)
    manifest = run_root / "manifests/worldfork_short_manifest.jsonl"
    for info in run_completed:
        job = info["job"]
        case_id = info["case_id"]
        condition = info["condition"]
        out_dir = info["out_dir"]
        result_payload = job.get("result") or {}
        status = "completed" if job.get("status") == "succeeded" else str(job.get("status"))
        if job.get("status") == "succeeded":
            _capture_run_artifacts(client, out_dir, info["big_bang_id"])
            path_mass = json.loads((out_dir / "path_mass.json").read_text(encoding="utf-8"))
            append_jsonl(output, _annotate_prediction(extract_worldfork_forecast(case_id, condition, path_mass), args))
        append_jsonl(
            manifest,
            worldfork_short_manifest_row(
                case_id=case_id,
                condition=condition,
                big_bang_id=str(info["big_bang_id"]),
                init_job_id=str(info["init_job_id"]),
                run_job_id=str(info["job_id"]),
                status=status,
                init_wait_seconds=float(info["init_wait_seconds"]),
                run_wait_seconds=float(info["wait_seconds"]),
                run_dir=info["relative_dir"],
                ticks_run=int(result_payload.get("ticks_run") or 0),
                multiverse_count=int(result_payload.get("multiverse_count") or 0),
                final_report_version_id=result_payload.get("final_report_version_id"),
                max_ticks_requested=args.max_ticks,
                tick_duration_minutes=args.tick_duration_minutes,
                route_policy_id=args.route_policy_id,
                prediction_output=_display_run_path(output, run_root),
            ),
        )
        print(json.dumps({"case_id": case_id, "condition": condition, "status": status, "ticks_run": result_payload.get("ticks_run")}))


def run_init_jobs(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    case_ids = _case_ids_from_manifest(run_root, args.case_ids, args.case_limit)
    if not case_ids:
        raise SystemExit("no case IDs selected")

    condition = args.condition
    submitted: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        relative_dir = Path(args.output_prefix) / case_id
        out_dir = run_root / relative_dir
        if (out_dir / "job_wait.json").exists() and init_artifacts_complete(out_dir) and not args.force:
            print(json.dumps({"case_id": case_id, "status": "skipped_existing", "out_dir": str(out_dir)}))
            continue
        if (out_dir / "job_id.txt").exists() and not args.force:
            job_id = (out_dir / "job_id.txt").read_text(encoding="utf-8").strip()
            print(json.dumps({"case_id": case_id, "job_id": job_id, "status": "resuming_existing"}))
        else:
            case_file = resolve_case_file(run_root, case_id)
            payload = build_init_job_payload(
                case_id=case_id,
                case_file=case_file,
                name_prefix=args.name_prefix,
                max_ticks=args.max_ticks,
                tick_duration_minutes=args.tick_duration_minutes,
                branch_policy=NO_BRANCH_POLICY,
            )
            _write_json(out_dir / "job_payload.json", payload)
            job, create_seconds = _timed_api_call(client, "POST", "/jobs", payload=payload)
            _write_json(out_dir / "job_create.json", job)
            (out_dir / "job_create_time_and_stderr.txt").write_text(f"real {create_seconds:.2f}\n", encoding="utf-8")
            job_id = str(job.get("id"))
            (out_dir / "job_id.txt").write_text(job_id + "\n", encoding="utf-8")
            print(json.dumps({"case_id": case_id, "job_id": job_id, "status": "submitted"}))
        submitted[case_id] = {
            "job_id": job_id,
            "out_dir": out_dir,
            "relative_dir": relative_dir,
            "submitted_at": time.monotonic(),
        }

    if not submitted:
        return

    _write_json(run_root / "setup/init_jobs_queues_after_submit.json", client.request("GET", "/jobs/queues"))
    _write_json(run_root / "setup/init_jobs_workers_after_submit.json", client.request("GET", "/jobs/workers"))

    deadline = time.monotonic() + args.wait_timeout
    pending = dict(submitted)
    while pending:
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for init jobs: {', '.join(sorted(pending))}")
        for case_id, info in list(pending.items()):
            job = client.request("GET", f"/jobs/{info['job_id']}")
            _write_json(info["out_dir"] / "job_status_latest.json", job)
            if not _job_finished(job):
                continue
            wait_seconds = time.monotonic() - float(info["submitted_at"])
            _write_json(info["out_dir"] / "job_wait.json", {"ok": True, "data": job, "meta": {"terminal": True}})
            (info["out_dir"] / "job_wait_time_and_stderr.txt").write_text(
                f"real {wait_seconds:.2f}\n",
                encoding="utf-8",
            )
            if job.get("status") != "succeeded":
                append_jsonl(
                    run_root / "manifests/run_manifest.jsonl",
                    init_manifest_row(
                        case_id=case_id,
                        condition=condition,
                        big_bang_id="",
                        job_id=info["job_id"],
                        status=str(job.get("status")),
                        wait_seconds=wait_seconds,
                        run_dir=info["relative_dir"],
                        actor_count=0,
                        trait_count=0,
                        llm_log_count=0,
                    ),
                )
                pending.pop(case_id)
                continue
            result = job.get("result") or {}
            big_bang_id = str(result.get("big_bang_id"))
            (info["out_dir"] / "big_bang_id.txt").write_text(big_bang_id + "\n", encoding="utf-8")
            counts = _capture_init_artifacts(client, info["out_dir"], big_bang_id)
            append_jsonl(
                run_root / "manifests/run_manifest.jsonl",
                init_manifest_row(
                    case_id=case_id,
                    condition=condition,
                    big_bang_id=big_bang_id,
                    job_id=info["job_id"],
                    status="completed",
                    wait_seconds=wait_seconds,
                    run_dir=info["relative_dir"],
                    actor_count=counts.get("actors", 0),
                    trait_count=counts.get("traits", 0),
                    llm_log_count=counts.get("llm_logs", 0),
                ),
            )
            pending.pop(case_id)
            print(json.dumps({"case_id": case_id, "job_id": info["job_id"], "big_bang_id": big_bang_id, "status": "completed"}))
        if pending:
            time.sleep(args.poll_seconds)

    _write_json(run_root / "setup/init_jobs_queues_after_batch.json", client.request("GET", "/jobs/queues"))


def public_case_markdown(card: dict[str, Any]) -> str:
    case_id = card["case_id"]
    role = card.get("benchmark_role", card.get("category", "worldfork_case"))
    question = card.get("question")
    scenario = card.get("scenario_text") or card.get("prompt") or ""
    source_packet = card.get("source_packet") or []
    endpoints = card.get("candidate_endpoints") or card.get("endpoints") or []

    parts = [f"# Case {case_id}", f"Benchmark role: {role}"]
    if question:
        parts.extend(["", f"Forecast question: {question}"])
    if scenario:
        parts.extend(["", "## Scenario", "", scenario])
    if source_packet:
        parts.extend(["", "## Source Packet"])
        for index, source in enumerate(source_packet, 1):
            title = source.get("title") or source.get("source_type") or "source"
            date = source.get("date") or "undated"
            parts.extend(["", f"### Source {index}: {title} / {date}", "", source.get("text", "")])
    if endpoints:
        parts.extend(["", "## Candidate Endpoints"])
        for endpoint in endpoints:
            if isinstance(endpoint, dict):
                endpoint_id = endpoint.get("id", "endpoint")
                label = endpoint.get("label") or endpoint.get("description") or ""
                parts.append(f"- {endpoint_id}: {label}")
            else:
                parts.append(f"- {endpoint}")
    for key in [
        "expected_focus",
        "required_forecast_output",
        "leakage_mitigation",
        "rubric_location",
    ]:
        value = card.get(key)
        if not value:
            continue
        title = key.replace("_", " ").title()
        parts.extend(["", f"## {title}"])
        if isinstance(value, list):
            parts.extend(f"- {item}" for item in value)
        else:
            parts.append(str(value))
    return "\n".join(parts).rstrip() + "\n"


def prepare_cases(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    rows: list[dict[str, Any]] = []
    for source_path, out_subdir, group in [
        (EXISTING_72, run_root / "cases/existing_72", "existing_72"),
        (PUBLIC_36, run_root / "cases/additional_36", "additional_36"),
    ]:
        for card in read_jsonl(source_path):
            case_id = card["case_id"]
            path = out_subdir / f"{case_id}.md"
            path.write_text(public_case_markdown(card), encoding="utf-8")
            rows.append(
                {
                    "case_id": case_id,
                    "group": group,
                    "benchmark_role": card.get("benchmark_role", card.get("category")),
                    "category": card.get("category"),
                    "difficulty": card.get("difficulty"),
                    "path": str(path.relative_to(run_root)),
                    "sha256": sha256(path),
                }
            )
    write_jsonl(run_root / "manifests/benchmark_case_manifest.jsonl", rows)
    readme = run_root / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# WorldFork ICML Forecasting Run",
                "",
                f"Created: {datetime.now(UTC).isoformat()}",
                "",
                "This run directory was prepared from public benchmark inputs only.",
                "Private evaluation data is not included in `cases/`.",
                "",
                "## ETA Snapshot",
                "",
                "- Static QA and case preparation: completed by this script.",
                "- Direct baselines: ETA depends on model/provider throughput; 24 cards x 2 conditions.",
                "- WorldFork short resolved runs: ETA depends on backend health and LLM latency; 24 cards x 2 conditions.",
                "- Long-horizon audit: highest-cost block; 18 cases x up to 35 ticks.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(run_root)


def card_qa(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    existing = read_jsonl(EXISTING_72)
    public = read_jsonl(PUBLIC_36)
    private = read_jsonl(PRIVATE_36)
    legacy = read_jsonl(LEGACY_36)
    matrix = json.loads(RUN_MATRIX.read_text(encoding="utf-8"))

    public_ids = [row["case_id"] for row in public]
    private_ids = [row["case_id"] for row in private]
    legacy_ids = [row["case_id"] for row in legacy]
    existing_ids = [row["case_id"] for row in existing]

    failures: list[str] = []
    warnings: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    require(len(existing) == 72, f"expected 72 existing cards, found {len(existing)}")
    require(len(public) == 36, f"expected 36 additional public cards, found {len(public)}")
    require(len(private) == 36, f"expected 36 private eval rows, found {len(private)}")
    require(len(legacy) == 36, f"expected 36 legacy rows, found {len(legacy)}")
    require(len(public_ids) == len(set(public_ids)), "duplicate public case_id")
    require(len(existing_ids) == len(set(existing_ids)), "duplicate existing case_id")
    require(public_ids == private_ids, "public and private case_id order mismatch")
    require(public_ids == legacy_ids, "public and legacy case_id order mismatch")

    private_field_names = {
        "resolution",
        "resolution_date",
        "resolution_summary",
        "resolution_sources",
        "entity_map",
        "gold_checklists",
        "scoring",
    }
    leaked = [row["case_id"] for row in public if private_field_names.intersection(row)]
    require(not leaked, f"public cards contain private fields: {', '.join(leaked)}")

    role_counts = Counter(row.get("benchmark_role") for row in public)
    require(role_counts["resolved_forecast"] == 24, f"expected 24 resolved cards, found {role_counts['resolved_forecast']}")
    require(role_counts["longform_dossier"] == 8, f"expected 8 dossier cards, found {role_counts['longform_dossier']}")
    require(
        role_counts["adversarial_calibration"] == 4,
        f"expected 4 calibration cards, found {role_counts['adversarial_calibration']}",
    )

    private_by_id = {row["case_id"]: row for row in private}
    for row in public:
        case_id = row["case_id"]
        endpoints = row.get("candidate_endpoints") or []
        sources = row.get("source_packet") or []
        role = row.get("benchmark_role")
        require(row.get("prompt"), f"{case_id}: missing prompt")
        require(row.get("scenario_text"), f"{case_id}: missing scenario_text")
        require(row.get("difficulty") in {"easy", "medium", "hard"}, f"{case_id}: invalid difficulty")
        if role == "resolved_forecast":
            require(len(endpoints) == 2, f"{case_id}: resolved card should have 2 endpoints")
            require(len(sources) >= 2, f"{case_id}: resolved card should include source packet")
            priv = private_by_id[case_id]
            require(priv.get("resolution") in {"yes", "no"}, f"{case_id}: invalid private binary resolution")
            require(bool(priv.get("resolution_date")), f"{case_id}: missing resolution_date")
            require(bool(priv.get("resolution_sources")), f"{case_id}: missing resolution_sources")
            if row.get("as_of_date") and priv.get("resolution_date") and row["as_of_date"] >= priv["resolution_date"]:
                failures.append(f"{case_id}: as_of_date is not before resolution_date")
        elif role == "longform_dossier":
            require(len(endpoints) >= 4, f"{case_id}: dossier should expose multiple endpoints")
            require(len(sources) >= 5, f"{case_id}: dossier should include a rich source packet")
            require(private_by_id[case_id].get("gold_checklists"), f"{case_id}: missing gold checklist")
        elif role == "adversarial_calibration":
            require(len(endpoints) >= 4, f"{case_id}: calibration should expose multiple endpoints")
            require(private_by_id[case_id].get("gold_checklists"), f"{case_id}: missing gold checklist")
        else:
            failures.append(f"{case_id}: unknown benchmark_role {role!r}")

    resolution_counts = Counter(row.get("resolution") for row in private if row.get("resolution"))
    require(resolution_counts == {"yes": 12, "no": 12}, f"resolved labels are not balanced: {dict(resolution_counts)}")

    matrix_ids = set()
    for value in matrix.get("case_groups", {}).values():
        if isinstance(value, list):
            matrix_ids.update(value)
    missing_from_inputs = sorted(matrix_ids - set(existing_ids) - set(public_ids))
    require(not missing_from_inputs, f"run matrix references missing case IDs: {missing_from_inputs}")

    source_rows: list[dict[str, str]] = []
    for row in private:
        if not row.get("resolution"):
            continue
        for source in row.get("resolution_sources") or []:
            source_rows.append(
                {
                    "case_id": row["case_id"],
                    "resolution": row["resolution"],
                    "resolution_date": row.get("resolution_date", ""),
                    "title": source.get("title", ""),
                    "url": source.get("url", ""),
                }
            )
    with (run_root / "results/resolution_sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "resolution", "resolution_date", "title", "url"])
        writer.writeheader()
        writer.writerows(source_rows)

    if args.offline_only:
        warnings.append("Resolution source URLs were not independently fetched; this is static package QA only.")

    report = [
        "# Card Quality Report",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Counts",
        "",
        f"- Existing public cards: {len(existing)}",
        f"- Additional public cards: {len(public)}",
        f"- Private eval rows: {len(private)}",
        f"- Legacy-schema rows: {len(legacy)}",
        f"- Additional role counts: {dict(role_counts)}",
        f"- Resolved label counts: {dict(resolution_counts)}",
        "",
        "## Leakage Separation",
        "",
        f"- Public/private IDs match: {public_ids == private_ids}",
        f"- Public/legacy IDs match: {public_ids == legacy_ids}",
        f"- Public cards with private fields: {leaked or 'none'}",
        "",
        "## Resolution Source Coverage",
        "",
        f"- Resolved cards with at least one source: {sum(1 for row in private if row.get('resolution') and row.get('resolution_sources'))}/24",
        "- Source inventory: `results/resolution_sources.csv`",
        "",
        "## Failures",
        "",
    ]
    report.extend(f"- {item}" for item in failures or ["none"])
    report.extend(["", "## Warnings", ""])
    report.extend(f"- {item}" for item in warnings or ["none"])
    report.extend(["", "## Verdict", ""])
    report.append("PASS" if not failures else "FAIL")
    report.append("")
    (run_root / "results/card_quality_report.md").write_text("\n".join(report), encoding="utf-8")
    print(run_root)
    if failures:
        raise SystemExit(1)


def clamp(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, p))


def score_forecasts(args: argparse.Namespace) -> None:
    predictions = read_jsonl(args.predictions)
    private = {row["case_id"]: row for row in read_jsonl(PRIVATE_36) if row.get("resolution") in {"yes", "no"}}
    rows: list[dict[str, Any]] = []
    for pred in predictions:
        case_id = pred["case_id"]
        if case_id not in private:
            continue
        resolution = private[case_id]["resolution"]
        p_yes = float(pred.get("p_yes", pred.get("forecast_distribution", {}).get("yes", 0.5)))
        p_no = float(pred.get("p_no", pred.get("forecast_distribution", {}).get("no", 1.0 - p_yes)))
        unresolved = float(pred.get("unresolved_mass", pred.get("forecast_distribution", {}).get("unresolved", 0.0)))
        if args.normalize_yes_no:
            denom = p_yes + p_no
            if denom > 0:
                p_yes = p_yes / denom
            else:
                p_yes = 0.5
                unresolved = 1.0
        y = 1.0 if resolution == "yes" else 0.0
        p_true = p_yes if resolution == "yes" else 1.0 - p_yes
        rows.append(
            {
                "case_id": case_id,
                "condition": pred.get("condition", args.condition),
                "resolution": resolution,
                "p_yes": f"{p_yes:.6f}",
                "brier": f"{(p_yes - y) ** 2:.6f}",
                "log_score": f"{-math.log(clamp(p_true)):.6f}",
                "unresolved_mass": f"{unresolved:.6f}",
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["case_id", "condition", "resolution", "p_yes", "brier", "log_score", "unresolved_mass"],
        )
        writer.writeheader()
        writer.writerows(rows)

    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)
    summary_rows = []
    for condition, items in sorted(by_condition.items()):
        summary_rows.append(
            {
                "condition": condition,
                "n": len(items),
                "mean_brier": statistics.fmean(float(item["brier"]) for item in items),
                "mean_log_score": statistics.fmean(float(item["log_score"]) for item in items),
                "mean_unresolved_mass": statistics.fmean(float(item["unresolved_mass"]) for item in items),
            }
        )
    print(json.dumps(summary_rows, indent=2, sort_keys=True))


def _direct_prompt(card: dict[str, Any], condition: str) -> tuple[str, str]:
    system = (
        "You are a calibrated forecasting assistant. You are evaluating a "
        "resolved-but-hidden event, but you do not know the resolution. Use only "
        "the public card text provided by the user. Do not use web search. Do not "
        "infer from real-world memory if the card is entity-masked. Return valid JSON only."
    )
    if condition == "structured_direct_llm":
        user = {
            "public_forecast_card": card,
            "task": "Use only this card. Return a calibrated yes/no forecast with evidence decomposition.",
            "output_contract": {
                "case_id": card["case_id"],
                "condition": condition,
                "evidence_for_yes": ["string"],
                "evidence_for_no": ["string"],
                "base_rate_or_analogies_from_card_only": ["string"],
                "key_uncertainties": ["string"],
                "p_yes": "number between 0 and 1",
                "p_no": "number between 0 and 1",
                "calibration_note": "string",
                "leakage_warning": "none|possible_real_world_memory|other",
            },
            "rules": [
                "p_yes + p_no must equal 1 within rounding tolerance.",
                "Do not mention or imply the true resolution.",
                "Do not use web or private evaluation data.",
            ],
        }
    else:
        user = {
            "public_forecast_card": card,
            "task": "Use only this card. Return one calibrated yes/no forecast and short rationale.",
            "output_contract": {
                "case_id": card["case_id"],
                "condition": condition,
                "p_yes": "number between 0 and 1",
                "p_no": "number between 0 and 1",
                "confidence": "low|medium|high",
                "main_drivers": ["string"],
                "main_uncertainties": ["string"],
                "leakage_warning": "none|possible_real_world_memory|other",
            },
            "rules": [
                "p_yes + p_no must equal 1 within rounding tolerance.",
                "Do not mention or imply the true resolution.",
                "Do not use web or private evaluation data.",
            ],
        }
    return system, json.dumps(user, ensure_ascii=False, sort_keys=True)


def _forecast_response_schema(condition: str) -> dict[str, Any]:
    common = {
        "case_id": {"type": "string"},
        "condition": {"type": "string", "enum": [condition]},
        "p_yes": {"type": "number", "minimum": 0, "maximum": 1},
        "p_no": {"type": "number", "minimum": 0, "maximum": 1},
        "leakage_warning": {"type": "string", "enum": ["none", "possible_real_world_memory", "other"]},
    }
    if condition == "structured_direct_llm":
        properties = {
            **common,
            "evidence_for_yes": {"type": "array", "items": {"type": "string"}},
            "evidence_for_no": {"type": "array", "items": {"type": "string"}},
            "base_rate_or_analogies_from_card_only": {"type": "array", "items": {"type": "string"}},
            "key_uncertainties": {"type": "array", "items": {"type": "string"}},
            "calibration_note": {"type": "string"},
        }
        required = list(properties)
    else:
        properties = {
            **common,
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "main_drivers": {"type": "array", "items": {"type": "string"}},
            "main_uncertainties": {"type": "array", "items": {"type": "string"}},
        }
        required = list(properties)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"{condition}_forecast",
            "schema": {
                "type": "object",
                "additionalProperties": True,
                "properties": properties,
                "required": required,
            },
        },
    }


async def _run_direct_baselines_async(args: argparse.Namespace) -> None:
    from backend.app.providers.openai_codex import OpenAICodexProvider
    from backend.app.schemas.common import Clock
    from backend.app.schemas.llm import ModelConfig, PromptPacket

    run_root = make_run_root(args.run_root)
    output = run_root / "raw/E2_direct_baselines/direct_predictions.jsonl"
    manifest_path = run_root / "manifests/direct_baseline_manifest.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    completed = set()
    if output.exists() and not args.force:
        for row in read_jsonl(output):
            completed.add((row.get("case_id"), row.get("condition")))

    cards = [row for row in read_jsonl(PUBLIC_36) if row.get("benchmark_role") == "resolved_forecast"]
    if args.case_ids:
        wanted = set(args.case_ids.split(","))
        cards = [row for row in cards if row["case_id"] in wanted]
    if args.case_limit:
        cards = cards[: args.case_limit]

    provider = OpenAICodexProvider(default_model=args.model, request_timeout=args.timeout)
    clock = Clock(
        current_tick=0,
        tick_duration_minutes=720,
        elapsed_minutes=0,
        previous_tick_minutes=None,
        max_schedule_horizon_ticks=4,
    )
    predictions_handle = output.open("a", encoding="utf-8")
    manifest_handle = manifest_path.open("a", encoding="utf-8")
    try:
        for card in cards:
            for condition in ["direct_llm", "structured_direct_llm"]:
                key = (card["case_id"], condition)
                if key in completed:
                    continue
                system, user_payload = _direct_prompt(card, condition)
                prompt = PromptPacket(
                    system=system,
                    clock=clock,
                    actor_id="direct_baseline",
                    actor_kind="god",
                    state={"public_card_json": card, "user_payload": user_payload},
                    output_schema_id=f"{condition}_forecast",
                    temperature=args.temperature,
                    metadata={"condition": condition, "case_id": card["case_id"], "private_eval_visible": False},
                )
                config = ModelConfig(
                    provider="openai-codex",
                    model=args.model,
                    fallback_model=args.model,
                    temperature=args.temperature,
                    top_p=1.0,
                    max_tokens=args.max_tokens,
                    response_format=_forecast_response_schema(condition),
                    timeout_seconds=int(args.timeout),
                    retry_policy="none",
                )
                started = datetime.now(UTC)
                result = await provider.generate_structured(prompt, config)
                payload = dict(result.parsed_json or {})
                payload["case_id"] = card["case_id"]
                payload["condition"] = condition
                p_yes = float(payload.get("p_yes", 0.5))
                p_no = float(payload.get("p_no", max(0.0, 1.0 - p_yes)))
                total = p_yes + p_no
                if total > 0 and abs(total - 1.0) > 0.001:
                    p_yes = p_yes / total
                    p_no = p_no / total
                    payload["p_yes"] = p_yes
                    payload["p_no"] = p_no
                    payload["normalization_note"] = "renormalized_to_sum_1"
                payload["_meta"] = {
                    "provider": result.provider,
                    "model_used": result.model_used,
                    "latency_ms": result.latency_ms,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "total_tokens": result.total_tokens,
                    "cost_usd": result.cost_usd,
                    "started_at": started.isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                }
                predictions_handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                predictions_handle.flush()
                manifest_handle.write(
                    json.dumps(
                        {
                            "case_id": card["case_id"],
                            "condition": condition,
                            "provider": result.provider,
                            "model_used": result.model_used,
                            "latency_ms": result.latency_ms,
                            "output_path": str(output.relative_to(run_root)),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                manifest_handle.flush()
                print(json.dumps({"case_id": card["case_id"], "condition": condition, "latency_ms": result.latency_ms}))
    finally:
        predictions_handle.close()
        manifest_handle.close()


def run_direct_baselines(args: argparse.Namespace) -> None:
    import asyncio

    asyncio.run(_run_direct_baselines_async(args))


def verify_sources(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    rows: list[dict[str, Any]] = []
    for case in read_jsonl(PRIVATE_36):
        if not case.get("resolution"):
            continue
        for source in case.get("resolution_sources") or []:
            url = source.get("url", "")
            status = "not_checked"
            http_status = ""
            final_url = ""
            error = ""
            title_hint = source.get("title", "")
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "WorldFork-ICML-source-check/0.1",
                        "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
                    },
                )
                with urllib.request.urlopen(request, timeout=args.timeout) as response:
                    http_status = str(getattr(response, "status", ""))
                    final_url = response.geturl()
                    content = response.read(args.bytes)
                    status = "ok" if http_status.startswith(("2", "3")) else "http_error"
                    lower = content.lower()
                    if b"<title" in lower:
                        start = lower.find(b"<title")
                        start = content.find(b">", start) + 1
                        end = lower.find(b"</title>", start)
                        if start > 0 and end > start:
                            title_hint = content[start:end].decode("utf-8", errors="replace").strip()
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout) as exc:
                status = "error"
                error = str(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    http_status = str(exc.code)
                    final_url = exc.url
            rows.append(
                {
                    "case_id": case["case_id"],
                    "resolution": case.get("resolution", ""),
                    "resolution_date": case.get("resolution_date", ""),
                    "source_title": source.get("title", ""),
                    "url": url,
                    "status": status,
                    "http_status": http_status,
                    "final_url": final_url,
                    "fetched_title_hint": " ".join(title_hint.split())[:240],
                    "error": error[:240],
                }
            )

    output = run_root / "results/source_verification.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "resolution",
                "resolution_date",
                "source_title",
                "url",
                "status",
                "http_status",
                "final_url",
                "fetched_title_hint",
                "error",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["status"] for row in rows)
    errors = [row for row in rows if row["status"] != "ok"]
    report = [
        "# Resolution Source Verification",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        f"- URLs checked: {len(rows)}",
        f"- Status counts: {dict(counts)}",
        "- Output CSV: `results/source_verification.csv`",
        "",
        "## Errors",
        "",
    ]
    if errors:
        report.extend(f"- {row['case_id']}: {row['http_status'] or row['status']} {row['url']} {row['error']}" for row in errors)
    else:
        report.append("- none")
    report.append("")
    (run_root / "results/source_verification.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"run_root": str(run_root), "counts": dict(counts)}, indent=2, sort_keys=True))
    if args.fail_on_error and errors:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare-cases", help="Generate public scenario files and manifest.")
    prepare.add_argument("--run-root", type=Path)
    prepare.set_defaults(func=prepare_cases)

    qa = sub.add_parser("card-qa", help="Run static card QA and write card_quality_report.md.")
    qa.add_argument("--run-root", type=Path)
    qa.add_argument("--offline-only", action="store_true", help="Record that URL/source verification was not fetched live.")
    qa.set_defaults(func=card_qa)

    score = sub.add_parser("score-forecasts", help="Score frozen forecast JSONL predictions.")
    score.add_argument("predictions", type=Path)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--condition", default="unknown")
    score.add_argument("--normalize-yes-no", action="store_true")
    score.set_defaults(func=score_forecasts)

    verify = sub.add_parser("verify-sources", help="Fetch private eval resolution source URLs and record status.")
    verify.add_argument("--run-root", type=Path)
    verify.add_argument("--timeout", type=float, default=20.0)
    verify.add_argument("--bytes", type=int, default=65536)
    verify.add_argument("--fail-on-error", action="store_true")
    verify.set_defaults(func=verify_sources)

    direct = sub.add_parser("run-direct-baselines", help="Run E2 direct baselines on public resolved cards.")
    direct.add_argument("--run-root", type=Path)
    direct.add_argument("--model", default="gpt-5.4")
    direct.add_argument("--temperature", type=float, default=0.2)
    direct.add_argument("--max-tokens", type=int, default=4096)
    direct.add_argument("--timeout", type=float, default=300.0)
    direct.add_argument("--case-limit", type=int)
    direct.add_argument("--case-ids", help="Comma-separated case IDs.")
    direct.add_argument("--force", action="store_true")
    direct.set_defaults(func=run_direct_baselines)

    init_jobs = sub.add_parser("run-init-jobs", help="Run queued E1 initialization jobs and capture artifacts.")
    init_jobs.add_argument("--run-root", type=Path, required=True)
    init_jobs.add_argument("--base-url", default="http://127.0.0.1:8003")
    init_jobs.add_argument("--api-prefix", default="/api")
    init_jobs.add_argument("--timeout", type=float, default=60.0)
    init_jobs.add_argument("--wait-timeout", type=float, default=1500.0)
    init_jobs.add_argument("--poll-seconds", type=float, default=5.0)
    init_jobs.add_argument("--case-ids", help="Comma-separated case IDs. Defaults to manifest order.")
    init_jobs.add_argument("--case-limit", type=int)
    init_jobs.add_argument("--condition", default="E1_init_job_codex_only")
    init_jobs.add_argument("--output-prefix", default="raw/E1_init_jobs")
    init_jobs.add_argument("--name-prefix", default="E1_init_job")
    init_jobs.add_argument("--max-ticks", type=int, default=1)
    init_jobs.add_argument("--tick-duration-minutes", type=int, default=720)
    init_jobs.add_argument("--force", action="store_true")
    init_jobs.set_defaults(func=run_init_jobs)

    short = sub.add_parser("run-worldfork-short", help="Run queued E3 short WorldFork resolved conditions.")
    short.add_argument("--run-root", type=Path, required=True)
    short.add_argument("--base-url", default="http://127.0.0.1:8003")
    short.add_argument("--api-prefix", default="/api")
    short.add_argument("--timeout", type=float, default=60.0)
    short.add_argument("--wait-timeout", type=float, default=3600.0)
    short.add_argument("--poll-seconds", type=float, default=10.0)
    short.add_argument("--case-ids", help="Comma-separated case IDs. Defaults to resolved_24 or core12 fallback.")
    short.add_argument("--case-limit", type=int)
    short.add_argument("--conditions", default="worldfork_no_branch_short,worldfork_branching_short")
    short.add_argument("--output-prefix", default="raw/E3_worldfork_short")
    short.add_argument("--prediction-output", default="raw/E3_worldfork_short/worldfork_predictions.jsonl")
    short.add_argument("--route-policy-id", help="Optional route-policy label to stamp into predictions and manifest rows.")
    short.add_argument("--name-prefix", default="E3")
    short.add_argument("--max-ticks", type=int, default=8)
    short.add_argument("--tick-duration-minutes", type=int, default=720)
    short.add_argument("--core12", action="store_true", help="Use the resolved core-12 fallback from the run matrix.")
    short.add_argument("--force", action="store_true")
    short.set_defaults(func=run_worldfork_short)

    short_batch = sub.add_parser("run-worldfork-short-batch", help="Run queued E3 short WorldFork conditions across cases in Celery batches.")
    short_batch.add_argument("--run-root", type=Path, required=True)
    short_batch.add_argument("--base-url", default="http://127.0.0.1:8003")
    short_batch.add_argument("--api-prefix", default="/api")
    short_batch.add_argument("--timeout", type=float, default=60.0)
    short_batch.add_argument("--wait-timeout", type=float, default=3600.0)
    short_batch.add_argument("--poll-seconds", type=float, default=10.0)
    short_batch.add_argument("--case-ids", help="Comma-separated case IDs. Defaults to resolved_24 or core12 fallback.")
    short_batch.add_argument("--case-limit", type=int)
    short_batch.add_argument("--conditions", default="worldfork_no_branch_short,worldfork_branching_short")
    short_batch.add_argument("--output-prefix", default="raw/E3_worldfork_short_batch")
    short_batch.add_argument("--prediction-output", default="raw/E3_worldfork_short/worldfork_predictions.jsonl")
    short_batch.add_argument("--route-policy-id", help="Optional route-policy label to stamp into predictions and manifest rows.")
    short_batch.add_argument("--name-prefix", default="E3_batch")
    short_batch.add_argument("--max-ticks", type=int, default=8)
    short_batch.add_argument("--tick-duration-minutes", type=int, default=720)
    short_batch.add_argument("--core12", action="store_true", help="Use the resolved core-12 fallback from the run matrix.")
    short_batch.add_argument("--force", action="store_true")
    short_batch.set_defaults(func=run_worldfork_short_batch)

    refresh_short = sub.add_parser("refresh-worldfork-short-ledgers", help="Re-evaluate big-bang endpoint ledgers and refresh E3 forecast predictions.")
    refresh_short.add_argument("--run-root", type=Path, required=True)
    refresh_short.add_argument("--base-url", default="http://127.0.0.1:8003")
    refresh_short.add_argument("--api-prefix", default="/api")
    refresh_short.add_argument("--timeout", type=float, default=60.0)
    refresh_short.add_argument("--case-ids", help="Optional comma-separated case IDs to refresh.")
    refresh_short.add_argument("--conditions", help="Optional comma-separated E3 conditions to refresh.")
    refresh_short.add_argument("--prediction-output", default="raw/E3_worldfork_short/worldfork_predictions.jsonl")
    refresh_short.add_argument("--route-policy-ids", help="Optional comma-separated route-policy IDs to refresh.")
    refresh_short.set_defaults(func=refresh_worldfork_short_ledgers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
