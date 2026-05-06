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
import random
import re
import socket
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
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
FORECAST_CARD_INITIALIZER_PROMPT = (
    "Build a compact WorldFork initialization state for a binary forecast-card benchmark. "
    "Prioritize the explicit question, source packet, candidate endpoints, and deadline. "
    "Use 3-4 total acting entities across actors/cohort_states/hero_archetypes/hero_states: "
    "one authority or scheduler, one affected public or customer cohort, one operational/risk actor, "
    "and only one optional observer/media/market actor if needed. Do not exceed 4 actors total, "
    "2 cohort_states, or 1 hero_state unless the public card explicitly names more necessary actors. "
    "Keep graph_edges <= 12, trait_vectors <= 8, initial_events <= 3, branch_hypotheses <= 2, "
    "merge_hypotheses <= 1, and risk_flags <= 3. For endpoint_ledger, align first to the explicit "
    "yes/no candidate endpoints; auxiliary diagnostic endpoints are optional and must not obscure "
    "the binary yes/no forecast. Keep all fields compact, evidence-grounded, and free of private "
    "resolution data."
)
NO_BRANCH_POLICY = {
    "branching_enabled": False,
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
    "min_branch_runway_ticks": 2,
}
LONG_BRANCH_POLICY = {
    "max_branch_depth": 3,
    "max_active_multiverses": 8,
    "max_branches_per_tick": 2,
    "branch_score_threshold": 0.75,
    "min_branch_runway_ticks": 2,
}
WORLDFORK_SHORT_POLICIES = {
    "worldfork_no_branch_short": NO_BRANCH_POLICY,
    "worldfork_branching_short": SHORT_BRANCH_POLICY,
}
WORLDFORK_LONG_POLICIES = {
    "worldfork_full_branching_long": LONG_BRANCH_POLICY,
}
PUBLIC_FORECAST_DEADLINES = {
    "resolved_001": "2025-10-10",
    "resolved_002": "2025-10-08",
    "resolved_005": "2026-03-15",
    "resolved_006": "2026-02-01",
    "resolved_007": "2025-11-01",
    "resolved_008": "2026-02-08",
    "resolved_009": "2026-02-01",
    "resolved_010": "2026-01-31",
    "resolved_011": "2025-11-04",
    "resolved_012": "2025-11-04",
    "resolved_013": "2025-11-04",
    "resolved_014": "2025-04-28",
    "resolved_022": "2026-01-31",
    "resolved_023": "2025-11-22",
    "resolved_024": "2025-09-30",
}
E4_DEFAULT_INPUT_PREFIX = Path("raw/E4_minimum_long_horizon_6")
E4_LONG_HORIZON_MANIFEST = Path("manifests/worldfork_long_horizon_manifest.jsonl")
E4_TERMINAL_STATUS_MAP = {
    "completed": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "interrupted": "interrupted",
    "interrupt_requested": "interrupted",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
E4_LEDGER_NATURAL_STOP_REASONS = {"completed", "all_multiverses_terminal"}
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


def _public_cards_by_id() -> dict[str, dict[str, Any]]:
    return {str(row.get("case_id")): row for row in read_jsonl(PUBLIC_36)}


def _public_card_for_case(case_id: str) -> dict[str, Any] | None:
    return _public_cards_by_id().get(case_id)


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _public_forecast_deadline(card: dict[str, Any]) -> str | None:
    case_id = str(card.get("case_id") or "")
    mapped = PUBLIC_FORECAST_DEADLINES.get(case_id)
    if mapped:
        return mapped
    as_of = _parse_iso_date(card.get("as_of_date"))
    text = "\n".join(
        str(card.get(key) or "")
        for key in ("forecast_horizon", "question", "prompt", "scenario_text")
    )
    candidates: list[date] = []
    for raw in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text):
        parsed = _parse_iso_date(raw)
        if parsed is not None and (as_of is None or parsed > as_of):
            candidates.append(parsed)
    if not candidates:
        return None
    return min(candidates).isoformat()


def _candidate_endpoints_for_case(case_id: str) -> list[dict[str, Any]]:
    card = _public_card_for_case(case_id) or {}
    endpoints = card.get("candidate_endpoints")
    if not isinstance(endpoints, list):
        return []
    return [endpoint for endpoint in endpoints if isinstance(endpoint, dict)]


def candidate_endpoint_keys_for_case(case_id: str) -> list[str]:
    keys: list[str] = []
    for endpoint in _candidate_endpoints_for_case(case_id):
        key = str(endpoint.get("id") or endpoint.get("endpoint_key") or "").strip().lower()
        if key and key not in keys:
            keys.append(key)
    return keys


def resolved_forecast_runtime_context(
    *,
    case_id: str,
    max_ticks: int,
    base_tick_duration_minutes: int,
    deadline_aware: bool = True,
) -> dict[str, Any]:
    card = _public_card_for_case(case_id) or {}
    as_of = _parse_iso_date(card.get("as_of_date"))
    deadline = _parse_iso_date(_public_forecast_deadline(card))
    tick_duration = int(base_tick_duration_minutes)
    horizon_days: int | None = None
    if deadline_aware and as_of is not None and deadline is not None and deadline >= as_of and max_ticks > 0:
        horizon_days = max(1, (deadline - as_of).days + 1)
        tick_duration = max(tick_duration, math.ceil((horizon_days * 24 * 60) / max_ticks))
    metadata = {
        "benchmark_role": card.get("benchmark_role"),
        "as_of_date": card.get("as_of_date"),
        "forecast_horizon": card.get("forecast_horizon"),
        "forecast_deadline_date": deadline.isoformat() if deadline else None,
        "deadline_horizon_days": horizon_days,
        "deadline_tick": int(max_ticks) if deadline and horizon_days is not None else None,
        "tick_horizon_policy": "deadline_aware" if deadline_aware and horizon_days is not None else "fixed_tick_duration",
    }
    endpoints = _candidate_endpoints_for_case(case_id)
    source_packet = card.get("source_packet") if isinstance(card.get("source_packet"), list) else []
    return {
        "tick_duration_minutes": tick_duration,
        "forecast_metadata": metadata,
        "question": card.get("question"),
        "scenario_text": card.get("scenario_text"),
        "source_packet": source_packet,
        "candidate_endpoints": endpoints,
        "endpoint_resolution_keys": candidate_endpoint_keys_for_case(case_id),
    }


def build_init_job_payload(
    *,
    case_id: str,
    case_file: Path,
    name_prefix: str,
    max_ticks: int,
    tick_duration_minutes: int,
    branch_policy: dict[str, Any],
    forecast_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario_input: dict[str, Any] = {}
    if forecast_context:
        scenario_input = {
            "forecast_metadata": forecast_context.get("forecast_metadata") or {},
            "question": forecast_context.get("question"),
            "scenario_text": forecast_context.get("scenario_text"),
            "source_packet": forecast_context.get("source_packet") or [],
            "candidate_endpoints": forecast_context.get("candidate_endpoints") or [],
            "endpoint_resolution_keys": forecast_context.get("endpoint_resolution_keys") or [],
        }
        scenario_input = {key: value for key, value in scenario_input.items() if value not in (None, "", [], {})}
    return {
        "job_type": "initialize_big_bang",
        "payload": {
            "name": f"{name_prefix}_{case_id}",
            "scenario_text": case_file.read_text(encoding="utf-8"),
            "scenario_input": scenario_input,
            "simulation_config": {
                "max_ticks": max_ticks,
                "tick_duration_minutes": tick_duration_minutes,
            },
            "branch_policy": branch_policy,
            "actors": [],
            "cohorts": [],
            "heroes": [],
            "use_initializer_agent": True,
            "initializer_prompt": FORECAST_CARD_INITIALIZER_PROMPT,
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
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        payload = {"ok": False, "error": {"type": type(exc).__name__, "reason": str(exc)}}
    _write_json(out_dir / "endpoint_ledger_evaluate.json", payload)


def _evaluate_multiverse_endpoint_ledger(
    client: ApiClient,
    out_dir: Path,
    multiverse: dict[str, Any],
    *,
    candidate_endpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    multiverse_id = str(multiverse.get("id") or "")
    ui_label = str(multiverse.get("ui_label") or multiverse.get("name") or multiverse_id or "multiverse")
    candidate_key = str((candidate_endpoint or {}).get("id") or (candidate_endpoint or {}).get("endpoint_key") or "").strip()
    file_label = f"{ui_label}_{candidate_key}" if candidate_key else ui_label
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", file_label).strip("_") or "multiverse"
    payload = {"run_inline": True}
    if candidate_endpoint:
        payload["candidate_endpoint"] = candidate_endpoint
    try:
        response_payload = client.request(
            "POST",
            f"/multiverses/{multiverse_id}/endpoint-ledgers/evaluate",
            payload=payload,
        )
    except urllib.error.HTTPError as exc:
        response_payload = {
            "ok": False,
            "error": {
                "type": "http_error",
                "status": exc.code,
                "reason": exc.reason,
                "multiverse_id": multiverse_id,
                "ui_label": ui_label,
            },
        }
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        response_payload = {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "reason": str(exc),
                "multiverse_id": multiverse_id,
                "ui_label": ui_label,
            },
        }
    result = {
        "multiverse_id": multiverse_id,
        "ui_label": ui_label,
        "candidate_endpoint": candidate_endpoint,
        "response": response_payload,
    }
    _write_json(out_dir / "posthoc_multiverse_endpoint_ledgers" / f"{safe_label}.json", result)
    return result


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
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            payload = {"ok": False, "error": {"type": type(exc).__name__, "reason": str(exc), "path": path}}
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


def _latest_completed_worldfork_short_runs(
    manifest_path: Path,
    *,
    source_route_policy_id: str | None,
    source_prediction_output: str | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    if not manifest_path.exists():
        return runs
    for row in read_jsonl(manifest_path):
        if row.get("status") != "completed":
            continue
        if source_route_policy_id is not None and str(row.get("route_policy_id") or "") != source_route_policy_id:
            continue
        if source_prediction_output is not None and str(row.get("prediction_output") or "") != source_prediction_output:
            continue
        key = (str(row.get("case_id") or ""), str(row.get("condition") or ""))
        if not all(key):
            continue
        runs[key] = row
    return runs


def _prediction_rows_by_key(
    path: Path,
    *,
    route_policy_id: str | None,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not path.exists():
        return rows
    for row in read_jsonl(path):
        key = _prediction_key(row)
        if route_policy_id is not None and key[2] != route_policy_id:
            continue
        rows[key] = row
    return rows


def _read_id_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _parse_job_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _job_wall_seconds(job: dict[str, Any]) -> float:
    started = _parse_job_timestamp(job.get("started_at") or job.get("created_at"))
    finished = _parse_job_timestamp(job.get("finished_at") or job.get("updated_at"))
    if started is None or finished is None:
        return 0.0
    return max(0.0, (finished - started).total_seconds())


def _artifact_wait_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("data") if isinstance(payload, dict) else None
    return _job_wall_seconds(data if isinstance(data, dict) else {})


def _read_json_artifact(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_artifact_error": f"invalid_json:{path.name}"}


def _unwrap_artifact_data(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


def _artifact_list(payload: Any, key: str | None = None) -> list[Any]:
    data = _unwrap_artifact_data(payload)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if key and isinstance(data.get(key), list):
            return data[key]
        nested = data.get("data")
        if isinstance(nested, list):
            return nested
        if key and isinstance(nested, dict) and isinstance(nested.get(key), list):
            return nested[key]
    return []


def _artifact_dict(payload: Any) -> dict[str, Any]:
    data = _unwrap_artifact_data(payload)
    return data if isinstance(data, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    number = _float_or_none(value)
    return int(number) if number is not None else 0


def _fmt_float(value: Any, digits: int = 6) -> str:
    number = _float_or_none(value)
    return "" if number is None else f"{number:.{digits}f}"


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _status_to_e4_terminal_state(status: Any) -> str | None:
    return E4_TERMINAL_STATUS_MAP.get(str(status or "").strip().lower())


def _e4_job_artifact(out_dir: Path) -> dict[str, Any]:
    for name in ["run_job_status_latest.json", "run_job_wait.json", "run_job_create.json"]:
        payload = _read_json_artifact(out_dir / name)
        data = _artifact_dict(payload)
        if data:
            return data
    return {}


def _e4_terminal_state(row: dict[str, Any], out_dir: Path) -> str | None:
    job_state = _status_to_e4_terminal_state(_e4_job_artifact(out_dir).get("status"))
    if job_state:
        return job_state
    return _status_to_e4_terminal_state(row.get("status"))


def _e4_resolve_run_dir(
    row: dict[str, Any],
    *,
    run_root: Path,
    input_prefix: Path,
) -> tuple[Path, Path]:
    raw_value = str(row.get("run_dir") or "")
    if raw_value:
        path = Path(raw_value)
    else:
        path = input_prefix / str(row.get("condition") or "") / str(row.get("case_id") or "")
    out_dir = path if path.is_absolute() else run_root / path
    relative = Path(_display_run_path(out_dir, run_root))
    return out_dir, relative


def _path_is_under_prefix(path: Path, prefix: Path) -> bool:
    try:
        path.relative_to(prefix)
        return True
    except ValueError:
        return False


def _e4_discovered_rows(run_root: Path, input_prefix: Path) -> list[dict[str, Any]]:
    base = run_root / input_prefix
    if not base.exists():
        return []
    rows: list[dict[str, Any]] = []
    for condition_dir in sorted(item for item in base.iterdir() if item.is_dir()):
        for case_dir in sorted(item for item in condition_dir.iterdir() if item.is_dir()):
            job = _e4_job_artifact(case_dir)
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            rows.append(
                {
                    "case_id": case_dir.name,
                    "condition": condition_dir.name,
                    "status": job.get("status", ""),
                    "big_bang_id": job.get("big_bang_id") or result.get("big_bang_id") or _read_id_file(case_dir / "big_bang_id.txt"),
                    "run_job_id": job.get("id") or _read_id_file(case_dir / "run_job_id.txt"),
                    "run_dir": str(input_prefix / condition_dir.name / case_dir.name),
                    "ticks_run": result.get("ticks_run"),
                    "multiverse_count": result.get("multiverse_count"),
                    "max_total_ticks_requested": (result.get("progress") or {}).get("requested_ticks"),
                }
            )
    return rows


def _e4_candidate_rows(run_root: Path, input_prefix: Path, manifest_path: Path) -> list[dict[str, Any]]:
    manifest = manifest_path if manifest_path.is_absolute() else run_root / manifest_path
    rows = read_jsonl(manifest) if manifest.exists() else []
    discovered = _e4_discovered_rows(run_root, input_prefix)
    seen = {
        (
            str(row.get("case_id") or ""),
            str(row.get("condition") or ""),
            str(row.get("run_dir") or ""),
        )
        for row in rows
    }
    for row in discovered:
        key = (str(row.get("case_id") or ""), str(row.get("condition") or ""), str(row.get("run_dir") or ""))
        if key not in seen:
            rows.append(row)
            seen.add(key)
    return rows


def _e4_ledger_resolved_from_path_mass(path_mass_payload: Any) -> bool:
    endpoint_rows = _artifact_list(path_mass_payload, "endpoint_path_mass_distribution")
    if not endpoint_rows:
        return False
    open_mass = 0.0
    closed_mass = 0.0
    for endpoint in endpoint_rows:
        if not isinstance(endpoint, dict):
            continue
        status_masses = endpoint.get("status_path_masses") if isinstance(endpoint.get("status_path_masses"), dict) else {}
        open_mass += float(status_masses.get("unresolved") or 0.0)
        open_mass += float(status_masses.get("insufficient_ticks") or 0.0)
        closed_mass += float(status_masses.get("realized") or 0.0)
        closed_mass += float(status_masses.get("eliminated") or 0.0)
    return closed_mass > 0 and open_mass == 0.0


def _e4_run_meta(
    row: dict[str, Any],
    *,
    run_root: Path,
    input_prefix: Path,
    path_mass_payload: Any,
) -> dict[str, Any]:
    out_dir, relative_dir = _e4_resolve_run_dir(row, run_root=run_root, input_prefix=input_prefix)
    job = _e4_job_artifact(out_dir)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
    terminal_state = _e4_terminal_state(row, out_dir) or ""
    stopped_reason = str(result.get("stopped_reason") or row.get("stopped_reason") or "")
    ticks_run = _int_value(result.get("ticks_run", row.get("ticks_run")))
    requested = _int_value(
        progress.get("requested_ticks")
        or row.get("max_total_ticks_requested")
        or (job.get("payload") or {}).get("max_total_ticks")
    )
    ledger_resolved = (
        terminal_state == "succeeded"
        and (
            stopped_reason in E4_LEDGER_NATURAL_STOP_REASONS
            or bool(row.get("final_report_version_id"))
            or _e4_ledger_resolved_from_path_mass(path_mass_payload)
        )
    )
    return {
        "case_id": str(row.get("case_id") or ""),
        "condition": str(row.get("condition") or ""),
        "route_policy_id": str(row.get("route_policy_id") or ""),
        "big_bang_id": str(row.get("big_bang_id") or job.get("big_bang_id") or result.get("big_bang_id") or ""),
        "run_job_id": str(row.get("run_job_id") or job.get("id") or ""),
        "terminal_state": terminal_state,
        "natural_stop_reason": stopped_reason,
        "natural_stop_ledger_resolved": "true" if ledger_resolved else "false",
        "hit_tick_cap": "true" if requested and ticks_run >= requested else "false",
        "ticks_run": ticks_run,
        "max_ticks_requested": _int_value(row.get("max_ticks_requested")),
        "max_total_ticks_requested": requested,
        "multiverse_count": _int_value(result.get("multiverse_count", row.get("multiverse_count"))),
        "run_dir": str(relative_dir),
        "out_dir": out_dir,
    }


def _e4_audit_rows(meta: dict[str, Any], path_mass_payload: Any) -> list[dict[str, Any]]:
    path_data = _artifact_dict(path_mass_payload)
    endpoint_rows = _artifact_list(path_mass_payload, "endpoint_path_mass_distribution")
    if not endpoint_rows:
        return [
            {
                **{key: value for key, value in meta.items() if key != "out_dir"},
                "ledger_version_id": "",
                "endpoint_key": "",
                "endpoint_label": "",
                "endpoint_status": "",
                "path_mass": "",
                "realized_mass": "",
                "eliminated_mass": "",
                "unresolved_mass": "",
                "insufficient_ticks_mass": "",
                "audit_traceability_score": "0.000000",
                "score_kind": "artifact_traceability_coverage",
                "ledger_artifact_present": "false",
            }
        ]
    rows: list[dict[str, Any]] = []
    for endpoint in endpoint_rows:
        if not isinstance(endpoint, dict):
            continue
        status_masses = endpoint.get("status_path_masses") if isinstance(endpoint.get("status_path_masses"), dict) else {}
        coverage = statistics.fmean(
            [
                bool(endpoint.get("endpoint_key")),
                bool(endpoint.get("label")),
                bool(endpoint.get("status")),
                _float_or_none(endpoint.get("path_mass")) is not None,
                bool(status_masses),
            ]
        )
        rows.append(
            {
                **{key: value for key, value in meta.items() if key != "out_dir"},
                "ledger_version_id": str(path_data.get("ledger_version_id") or ""),
                "endpoint_key": str(endpoint.get("endpoint_key") or ""),
                "endpoint_label": str(endpoint.get("label") or ""),
                "endpoint_status": str(endpoint.get("status") or ""),
                "path_mass": _fmt_float(endpoint.get("path_mass")),
                "realized_mass": _fmt_float(status_masses.get("realized")),
                "eliminated_mass": _fmt_float(status_masses.get("eliminated")),
                "unresolved_mass": _fmt_float(status_masses.get("unresolved")),
                "insufficient_ticks_mass": _fmt_float(status_masses.get("insufficient_ticks")),
                "audit_traceability_score": _fmt_float(coverage),
                "score_kind": "artifact_traceability_coverage",
                "ledger_artifact_present": "true",
            }
        )
    return rows


def _trait_axis_values(traits: list[Any], axis: str) -> list[float]:
    values: list[float] = []
    for item in traits:
        if not isinstance(item, dict):
            continue
        vector = item.get("trait_vector") if isinstance(item.get("trait_vector"), dict) else {}
        behavior = vector.get("behavior_axes") if isinstance(vector.get("behavior_axes"), dict) else {}
        value = _float_or_none(behavior.get(axis))
        if value is not None:
            values.append(value)
    return values


def _e4_social_row(meta: dict[str, Any]) -> dict[str, Any]:
    out_dir = meta["out_dir"]
    actors_payload = _read_json_artifact(out_dir / "actors.json")
    traits_payload = _read_json_artifact(out_dir / "traits.json")
    graphs_payload = _read_json_artifact(out_dir / "graphs.json")
    sociology_payload = _read_json_artifact(out_dir / "sociology_baseline.json")
    emotion_payload = _read_json_artifact(out_dir / "emotion_baseline.json")
    actors = _artifact_list(actors_payload, "actors")
    traits = _artifact_list(traits_payload, "traits")
    graphs = _artifact_dict(graphs_payload)
    sociology = _artifact_dict(sociology_payload)
    emotion = _artifact_dict(emotion_payload)
    edges = graphs.get("edges") if isinstance(graphs.get("edges"), list) else []
    nodes = graphs.get("nodes") if isinstance(graphs.get("nodes"), list) else []
    signals = sociology.get("signals") if isinstance(sociology.get("signals"), list) else []
    prompt_influences = sociology.get("prompt_influences") if isinstance(sociology.get("prompt_influences"), list) else []
    snapshots = emotion.get("snapshots") if isinstance(emotion.get("snapshots"), list) else []
    observations = emotion.get("observations") if isinstance(emotion.get("observations"), list) else []
    edge_weights = [_float_or_none(edge.get("weight")) for edge in edges if isinstance(edge, dict)]
    signal_levels = [
        _float_or_none((signal.get("signal") or {}).get("level"))
        for signal in signals
        if isinstance(signal, dict) and isinstance(signal.get("signal"), dict)
    ]
    present = [
        actors_payload is not None,
        traits_payload is not None,
        graphs_payload is not None,
        sociology_payload is not None,
        emotion_payload is not None,
    ]
    return {
        **{key: value for key, value in meta.items() if key != "out_dir"},
        "actor_count": len(actors),
        "active_actor_count": sum(1 for actor in actors if isinstance(actor, dict) and actor.get("status") == "active"),
        "trait_actor_count": len(traits),
        "graph_node_count": len(nodes),
        "graph_edge_count": len(edges),
        "mean_graph_edge_weight": _fmt_float(_mean([value for value in edge_weights if value is not None])),
        "signal_count": len(signals),
        "prompt_influence_count": len(prompt_influences),
        "emotion_snapshot_count": len(snapshots),
        "emotion_observation_count": len(observations),
        "mean_social_signal_level": _fmt_float(_mean([value for value in signal_levels if value is not None])),
        "mean_behavior_assertiveness": _fmt_float(_mean(_trait_axis_values(traits, "assertiveness"))),
        "mean_behavior_caution": _fmt_float(_mean(_trait_axis_values(traits, "caution"))),
        "mean_behavior_coordination": _fmt_float(_mean(_trait_axis_values(traits, "coordination"))),
        "mean_behavior_responsiveness": _fmt_float(_mean(_trait_axis_values(traits, "responsiveness"))),
        "social_traceability_score": _fmt_float(statistics.fmean(present)),
        "score_kind": "artifact_presence_and_summary_coverage",
    }


def _e4_cost_summary(out_dir: Path) -> dict[str, Any]:
    for name in ["cost.json", "timing.json"]:
        payload = _artifact_dict(_read_json_artifact(out_dir / name))
        if not payload:
            continue
        summary = payload.get("cost_summary") if isinstance(payload.get("cost_summary"), dict) else payload
        if isinstance(summary, dict):
            return summary
    return {}


def _e4_runtime_cost_row(meta: dict[str, Any]) -> dict[str, Any]:
    summary = _e4_cost_summary(meta["out_dir"])
    actual = summary.get("actual") if isinstance(summary.get("actual"), dict) else {}
    estimated = summary.get("estimated") if isinstance(summary.get("estimated"), dict) else {}
    tokens = summary.get("tokens") if isinstance(summary.get("tokens"), dict) else {}
    time_actual = summary.get("time_actual") if isinstance(summary.get("time_actual"), dict) else {}
    return {
        **{key: value for key, value in meta.items() if key != "out_dir"},
        "actual_openrouter_usd": _fmt_float(actual.get("openrouter_usd")),
        "estimated_including_non_openrouter_usd": _fmt_float(estimated.get("including_non_openrouter_usd")),
        "total_tokens": _int_value(tokens.get("total_tokens")),
        "call_count": _int_value(summary.get("call_count")),
        "total_llm_duration_seconds": _fmt_float(time_actual.get("total_llm_duration_seconds")),
        "max_llm_duration_seconds": _fmt_float(time_actual.get("max_llm_duration_seconds")),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _markdown_cell(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _write_markdown_table(path: Path, rows: list[dict[str, Any]], fieldnames: list[str], *, max_rows: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(fieldnames) + " |", "| " + " | ".join(["---"] * len(fieldnames)) + " |"]
    for row in rows[:max_rows]:
        lines.append("| " + " | ".join(_markdown_cell(row.get(field, "")) for field in fieldnames) + " |")
    if len(rows) > max_rows:
        lines.append(f"\nShowing first {max_rows} of {len(rows)} rows.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bootstrap_mean_interval(values: list[float], *, iterations: int, seed: int) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "ci95_low": None, "ci95_high": None}
    if len(values) == 1 or iterations <= 0:
        mean = values[0]
        return {"n": 1, "mean": mean, "ci95_low": mean, "ci95_high": mean}
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(sample))
    means.sort()
    low_idx = max(0, min(len(means) - 1, int(0.025 * len(means))))
    high_idx = max(0, min(len(means) - 1, int(0.975 * len(means)) - 1))
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "ci95_low": means[low_idx],
        "ci95_high": means[high_idx],
    }


def _bootstrap_intervals(
    tables: dict[str, list[dict[str, Any]]],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    metrics = [
        "audit_traceability_score",
        "actor_count",
        "active_actor_count",
        "graph_edge_count",
        "mean_behavior_assertiveness",
        "mean_behavior_caution",
        "social_traceability_score",
        "actual_openrouter_usd",
        "estimated_including_non_openrouter_usd",
        "total_tokens",
        "call_count",
        "ticks_run",
    ]
    intervals: dict[str, Any] = {}
    for metric in metrics:
        values: list[float] = []
        for rows in tables.values():
            for row in rows:
                value = _float_or_none(row.get(metric))
                if value is not None:
                    values.append(value)
        if values:
            intervals[metric] = _bootstrap_mean_interval(values, iterations=iterations, seed=seed + len(intervals))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "bootstrap_iterations": iterations,
        "bootstrap_seed": seed,
        "statistic": "mean",
        "metrics": intervals,
    }


def generate_e4_paper_artifact_files(
    *,
    run_root: Path,
    input_prefix: Path = E4_DEFAULT_INPUT_PREFIX,
    manifest_path: Path = E4_LONG_HORIZON_MANIFEST,
    bootstrap_iterations: int = 2000,
    bootstrap_seed: int = 20260505,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    audit_rows: list[dict[str, Any]] = []
    social_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    skipped_nonterminal = 0

    for row in _e4_candidate_rows(run_root, input_prefix, manifest_path):
        out_dir, relative_dir = _e4_resolve_run_dir(row, run_root=run_root, input_prefix=input_prefix)
        if not _path_is_under_prefix(relative_dir, input_prefix):
            continue
        terminal_state = _e4_terminal_state(row, out_dir)
        if not terminal_state:
            skipped_nonterminal += 1
            continue
        path_mass_payload = _read_json_artifact(out_dir / "path_mass.json")
        meta = _e4_run_meta(row, run_root=run_root, input_prefix=input_prefix, path_mass_payload=path_mass_payload)
        status_counts[terminal_state] += 1
        audit_rows.extend(_e4_audit_rows(meta, path_mass_payload))
        social_rows.append(_e4_social_row(meta))
        runtime_rows.append(_e4_runtime_cost_row(meta))

    results_dir = run_root / "results"
    tables_dir = run_root / "paper/tables"
    common_fields = [
        "case_id",
        "condition",
        "route_policy_id",
        "big_bang_id",
        "run_job_id",
        "terminal_state",
        "natural_stop_reason",
        "natural_stop_ledger_resolved",
        "hit_tick_cap",
        "ticks_run",
        "max_ticks_requested",
        "max_total_ticks_requested",
        "multiverse_count",
        "run_dir",
    ]
    audit_fields = common_fields + [
        "ledger_version_id",
        "endpoint_key",
        "endpoint_label",
        "endpoint_status",
        "path_mass",
        "realized_mass",
        "eliminated_mass",
        "unresolved_mass",
        "insufficient_ticks_mass",
        "audit_traceability_score",
        "score_kind",
        "ledger_artifact_present",
    ]
    social_fields = common_fields + [
        "actor_count",
        "active_actor_count",
        "trait_actor_count",
        "graph_node_count",
        "graph_edge_count",
        "mean_graph_edge_weight",
        "signal_count",
        "prompt_influence_count",
        "emotion_snapshot_count",
        "emotion_observation_count",
        "mean_social_signal_level",
        "mean_behavior_assertiveness",
        "mean_behavior_caution",
        "mean_behavior_coordination",
        "mean_behavior_responsiveness",
        "social_traceability_score",
        "score_kind",
    ]
    runtime_fields = common_fields + [
        "actual_openrouter_usd",
        "estimated_including_non_openrouter_usd",
        "total_tokens",
        "call_count",
        "total_llm_duration_seconds",
        "max_llm_duration_seconds",
    ]
    runtime_output = results_dir / "e4_runtime_cost_summary.csv"
    bootstrap_output = results_dir / "e4_bootstrap_intervals.json"

    _write_csv(results_dir / "audit_scores.csv", audit_rows, audit_fields)
    _write_csv(results_dir / "social_state_scores.csv", social_rows, social_fields)
    _write_csv(runtime_output, runtime_rows, runtime_fields)

    intervals = _bootstrap_intervals(
        {
            "audit_scores": audit_rows,
            "social_state_scores": social_rows,
            "e4_runtime_cost_summary": runtime_rows,
        },
        iterations=bootstrap_iterations,
        seed=bootstrap_seed,
    )
    _write_json(bootstrap_output, intervals)

    _write_markdown_table(tables_dir / "audit_scores.md", audit_rows, audit_fields)
    _write_markdown_table(tables_dir / "social_state_scores.md", social_rows, social_fields)
    _write_markdown_table(tables_dir / "e4_runtime_cost_summary.md", runtime_rows, runtime_fields)
    summary = {
        "run_root": str(run_root),
        "input_prefix": str(input_prefix),
        "manifest_path": str(manifest_path),
        "terminal_runs": len(social_rows),
        "skipped_nonterminal_runs": skipped_nonterminal,
        "terminal_state_counts": dict(status_counts),
        "audit_rows": len(audit_rows),
        "social_rows": len(social_rows),
        "runtime_rows": len(runtime_rows),
        "outputs": {
            "audit_scores": str(results_dir / "audit_scores.csv"),
            "social_state_scores": str(results_dir / "social_state_scores.csv"),
            "e4_runtime_cost_summary": str(runtime_output),
            "e4_bootstrap_intervals": str(bootstrap_output),
            "paper_tables": str(tables_dir),
        },
        "notes": [
            "Artifact-only E4 generator; no API calls are made.",
            "Queued/running rows are skipped unless manifest or run_job_status_latest.json marks them terminal.",
            "natural_stop_ledger_resolved is distinct from hit_tick_cap; reaching a tick cap is not counted as success.",
        ],
    }
    _write_json(tables_dir / "e4_artifact_summary.json", summary)
    (tables_dir / "e4_artifact_summary.md").write_text(
        "\n".join(
            [
                "# E4 Artifact Summary",
                "",
                f"- Terminal runs: {summary['terminal_runs']}",
                f"- Skipped nonterminal runs: {summary['skipped_nonterminal_runs']}",
                f"- Terminal state counts: {summary['terminal_state_counts']}",
                "- Natural-stop ledger resolution is reported separately from tick-cap usage.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return summary


def generate_e4_paper_artifacts(args: argparse.Namespace) -> None:
    summary = generate_e4_paper_artifact_files(
        run_root=args.run_root,
        input_prefix=args.input_prefix,
        manifest_path=args.manifest,
        bootstrap_iterations=args.bootstrap_iterations,
        bootstrap_seed=args.bootstrap_seed,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def _manifest_run_job_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row.get("run_job_id") or "") for row in read_jsonl(path) if row.get("run_job_id")}


def _manifest_run_job_statuses(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    statuses: dict[str, str] = {}
    for row in read_jsonl(path):
        run_job_id = str(row.get("run_job_id") or "")
        if run_job_id:
            statuses[run_job_id] = str(row.get("status") or "")
    return statuses


def _worldfork_resume_targets(
    *,
    source_predictions: Path,
    output_predictions: Path,
    source_runs: dict[tuple[str, str], dict[str, Any]],
    source_route_policy_id: str,
    target_route_policy_id: str,
    conditions: set[str] | None,
    case_ids: set[str] | None,
    skip_resolved_unresolved_mass: float | None,
    max_ticks: int,
    force: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing = _prediction_rows_by_key(output_predictions, route_policy_id=target_route_policy_id)
    carried: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str, str]] = set()
    for source_row in read_jsonl(source_predictions):
        case_id = str(source_row.get("case_id") or "")
        condition = str(source_row.get("condition") or "")
        if not case_id or not condition:
            continue
        if case_ids and case_id not in case_ids:
            continue
        if conditions and condition not in conditions:
            continue
        if str(source_row.get("route_policy_id") or "") != source_route_policy_id:
            continue
        target_key = (case_id, condition, target_route_policy_id)
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        if target_key in existing and not force:
            print(json.dumps({"case_id": case_id, "condition": condition, "status": "skipped_existing_resume"}))
            continue
        source_run = source_runs.get((case_id, condition))
        if source_run is None:
            raise SystemExit(f"{case_id}/{condition}: no completed source run in manifest for resume")
        unresolved_mass = float(source_row.get("unresolved_mass") or 0.0)
        if skip_resolved_unresolved_mass is not None and unresolved_mass <= skip_resolved_unresolved_mass:
            carried_row = dict(source_row)
            carried_row["route_policy_id"] = target_route_policy_id
            carried_row["max_ticks_requested"] = max_ticks
            carried_row["resume_status"] = "carried_forward_resolved"
            carried_row["source_route_policy_id"] = source_route_policy_id
            carried_row["source_max_ticks_requested"] = source_row.get("max_ticks_requested")
            carried.append(carried_row)
            continue
        targets.append(
            {
                "case_id": case_id,
                "condition": condition,
                "big_bang_id": str(source_run.get("big_bang_id") or ""),
                "init_job_id": str(source_run.get("init_job_id") or ""),
                "source_run_dir": str(source_run.get("run_dir") or ""),
                "source_run_job_id": str(source_run.get("run_job_id") or ""),
                "source_max_ticks_requested": source_row.get("max_ticks_requested"),
            }
        )
    return carried, targets


def _latest_tick_index(ticks_payload: Any) -> int:
    if not isinstance(ticks_payload, list) or not ticks_payload:
        return 0
    return max(int(row.get("tick_index") or 0) for row in ticks_payload if isinstance(row, dict))


def _multiverse_runtime_max_ticks(multiverse: dict[str, Any]) -> int | None:
    state = multiverse.get("state") if isinstance(multiverse.get("state"), dict) else {}
    overrides = state.get("runtime_overrides") if isinstance(state.get("runtime_overrides"), dict) else {}
    for value in [
        overrides.get("max_ticks"),
        (overrides.get("simulation_config") or {}).get("max_ticks") if isinstance(overrides.get("simulation_config"), dict) else None,
    ]:
        if value is not None:
            return int(value)
    return None


def _resume_additional_ticks(*, latest_tick_index: int, target_max_ticks: int) -> int:
    return max(0, target_max_ticks - latest_tick_index)


def _resume_run_budget(*, latest_tick_index: int, target_max_ticks: int) -> int:
    return max(2, _resume_additional_ticks(latest_tick_index=latest_tick_index, target_max_ticks=target_max_ticks) + 2)


def _resume_job_idempotency_key(
    *,
    attempt_id: str,
    route_policy_id: str,
    condition: str,
    case_id: str,
    big_bang_id: str,
    max_ticks: int,
) -> str:
    raw = f"{attempt_id}:{route_policy_id}:{condition}:{case_id}:{big_bang_id}:max{max_ticks}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"icml_resume:{attempt_id}:{case_id}:max{max_ticks}:{digest}"


def _prepare_worldfork_resume(
    client: ApiClient,
    out_dir: Path,
    *,
    big_bang_id: str,
    target_max_ticks: int,
) -> tuple[int, list[dict[str, Any]]]:
    multiverses = client.request("GET", f"/big-bangs/{big_bang_id}/multiverses")
    if not isinstance(multiverses, list):
        raise SystemExit(f"{big_bang_id}: expected multiverse list")
    latest_indexes: list[int] = []
    continuation_rows: list[dict[str, Any]] = []
    for multiverse in multiverses:
        if not isinstance(multiverse, dict):
            continue
        status = str(multiverse.get("status") or "")
        multiverse_id = str(multiverse.get("id") or "")
        if not multiverse_id:
            continue
        ticks = client.request("GET", f"/multiverses/{multiverse_id}/ticks")
        latest_tick_index = _latest_tick_index(ticks)
        latest_indexes.append(latest_tick_index)
        current_max_ticks = _multiverse_runtime_max_ticks(multiverse)
        if status == "active" and current_max_ticks is not None and latest_tick_index < current_max_ticks:
            continuation_rows.append(
                {
                    "multiverse_id": multiverse_id,
                    "latest_tick_index": latest_tick_index,
                    "previous_max_ticks": current_max_ticks,
                    "target_max_ticks": target_max_ticks,
                    "continue_response": None,
                    "status": "active_before_current_horizon",
                }
            )
            continue
        if latest_tick_index > target_max_ticks:
            raise SystemExit(f"{big_bang_id}/{multiverse_id}: latest tick {latest_tick_index} exceeds target max {target_max_ticks}")
        if current_max_ticks is None or current_max_ticks < target_max_ticks:
            payload = {
                "max_ticks": target_max_ticks,
                "reason": "ICML E3 resume from existing capped run",
            }
            continued = client.request("POST", f"/multiverses/{multiverse_id}/continue", payload=payload)
            continuation_rows.append(
                {
                    "multiverse_id": multiverse_id,
                    "latest_tick_index": latest_tick_index,
                    "previous_max_ticks": current_max_ticks,
                    "target_max_ticks": target_max_ticks,
                    "continue_response": continued,
                }
            )
        else:
            continuation_rows.append(
                {
                    "multiverse_id": multiverse_id,
                    "latest_tick_index": latest_tick_index,
                    "previous_max_ticks": current_max_ticks,
                    "target_max_ticks": target_max_ticks,
                    "continue_response": None,
                    "status": "already_at_target_horizon",
                }
            )
    _write_json(out_dir / "resume_prepare.json", {"big_bang_id": big_bang_id, "multiverses": continuation_rows})
    if not latest_indexes:
        return 1, continuation_rows
    return max(_resume_run_budget(latest_tick_index=index, target_max_ticks=target_max_ticks) for index in latest_indexes), continuation_rows


def resume_worldfork_short_batch(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    attempt_id = args.resume_attempt_id or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    source_predictions = _prediction_output_path(run_root, args.source_prediction_output)
    output = _prediction_output_path(run_root, args.prediction_output)
    source_prediction_display = _display_run_path(source_predictions, run_root)
    manifest = run_root / "manifests/worldfork_short_manifest.jsonl"
    conditions = {item.strip() for item in args.conditions.split(",") if item.strip()} if args.conditions else None
    case_ids = {item.strip() for item in args.case_ids.split(",") if item.strip()} if args.case_ids else None
    skip_mass = None if args.skip_resolved_unresolved_mass < 0 else float(args.skip_resolved_unresolved_mass)
    source_runs = _latest_completed_worldfork_short_runs(
        manifest,
        source_route_policy_id=args.source_route_policy_id,
        source_prediction_output=source_prediction_display,
    )
    carried, targets = _worldfork_resume_targets(
        source_predictions=source_predictions,
        output_predictions=output,
        source_runs=source_runs,
        source_route_policy_id=args.source_route_policy_id,
        target_route_policy_id=args.route_policy_id,
        conditions=conditions,
        case_ids=case_ids,
        skip_resolved_unresolved_mass=skip_mass,
        max_ticks=args.max_ticks,
        force=args.force,
    )
    for row in carried:
        append_jsonl(output, row)
        print(json.dumps({"case_id": row["case_id"], "condition": row["condition"], "status": "carried_forward_resolved"}))
    if not targets:
        return

    run_pending: dict[str, dict[str, Any]] = {}
    for target in targets:
        case_id = target["case_id"]
        condition = target["condition"]
        big_bang_id = target["big_bang_id"]
        relative_dir = Path(args.output_prefix) / condition / case_id
        out_dir = run_root / relative_dir
        _write_json(out_dir / "resume_source.json", target)
        run_budget, continuation_rows = _prepare_worldfork_resume(
            client,
            out_dir,
            big_bang_id=big_bang_id,
            target_max_ticks=args.max_ticks,
        )
        run_payload = {"max_total_ticks": run_budget}
        endpoint_resolution_keys = candidate_endpoint_keys_for_case(case_id)
        if endpoint_resolution_keys:
            run_payload["endpoint_resolution_keys"] = endpoint_resolution_keys
        if getattr(args, "stop_when_endpoint_ledger_resolved", False):
            run_payload["stop_when_endpoint_ledger_resolved"] = True
        if not getattr(args, "generate_reports", False):
            run_payload["skip_reports"] = True
        _write_json(out_dir / "run_job_payload.json", run_payload)
        run_job, run_create_seconds = _timed_api_call(
            client,
            "POST",
            "/jobs",
            payload={
                "job_type": "run_big_bang_until_complete",
                "big_bang_id": big_bang_id,
                "payload": run_payload,
                "idempotency_key": _resume_job_idempotency_key(
                    attempt_id=attempt_id,
                    route_policy_id=args.route_policy_id,
                    condition=condition,
                    case_id=case_id,
                    big_bang_id=big_bang_id,
                    max_ticks=args.max_ticks,
                ),
            },
        )
        _write_json(out_dir / "run_job_create.json", run_job)
        (out_dir / "run_job_create_time_and_stderr.txt").write_text(f"real {run_create_seconds:.2f}\n", encoding="utf-8")
        run_job_id = str(run_job.get("id"))
        (out_dir / "run_job_id.txt").write_text(run_job_id + "\n", encoding="utf-8")
        label = f"{condition}/{case_id}"
        run_pending[label] = {
            **target,
            "relative_dir": relative_dir,
            "out_dir": out_dir,
            "job_id": run_job_id,
            "submitted_at": time.monotonic(),
            "run_budget": run_budget,
            "continuation_rows": continuation_rows,
        }
        print(
            json.dumps(
                {
                    "case_id": case_id,
                    "condition": condition,
                    "run_job_id": run_job_id,
                    "status": "resume_run_submitted",
                    "max_total_ticks": run_budget,
                }
            )
        )

    _write_json(run_root / "setup/worldfork_short_resume_queues_after_run_submit.json", client.request("GET", "/jobs/queues"))
    run_completed = _wait_many_jobs(
        client,
        run_pending,
        artifact_prefix="run_job",
        wait_timeout=args.wait_timeout,
        poll_seconds=args.poll_seconds,
    )

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
            prediction = extract_worldfork_forecast(
                case_id,
                condition,
                path_mass,
                candidate_endpoint_keys=candidate_endpoint_keys_for_case(case_id),
            )
            prediction["route_policy_id"] = args.route_policy_id
            prediction["source_route_policy_id"] = args.source_route_policy_id
            prediction["source_max_ticks_requested"] = info.get("source_max_ticks_requested")
            prediction["max_ticks_requested"] = args.max_ticks
            prediction["tick_duration_minutes"] = int(args.tick_duration_minutes)
            prediction["resume_status"] = "resumed_existing_big_bang"
            append_jsonl(output, prediction)
        manifest_row = worldfork_short_manifest_row(
            case_id=case_id,
            condition=condition,
            big_bang_id=str(info["big_bang_id"]),
            init_job_id=str(info.get("init_job_id") or ""),
            run_job_id=str(info["job_id"]),
            status=status,
            init_wait_seconds=0.0,
            run_wait_seconds=float(info["wait_seconds"]),
            run_dir=info["relative_dir"],
            ticks_run=int(result_payload.get("ticks_run") or 0),
            multiverse_count=int(result_payload.get("multiverse_count") or 0),
            final_report_version_id=result_payload.get("final_report_version_id"),
            max_ticks_requested=args.max_ticks,
            tick_duration_minutes=args.tick_duration_minutes,
            route_policy_id=args.route_policy_id,
            prediction_output=_display_run_path(output, run_root),
        )
        manifest_row["resume_source_run_dir"] = info.get("source_run_dir")
        manifest_row["resume_source_run_job_id"] = info.get("source_run_job_id")
        manifest_row["resume_run_budget"] = info.get("run_budget")
        append_jsonl(manifest, manifest_row)
        print(json.dumps({"case_id": case_id, "condition": condition, "status": status, "ticks_run": result_payload.get("ticks_run")}))


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


def _endpoint_key_value(entry: dict[str, Any]) -> str:
    return str(entry.get("endpoint_key") or entry.get("id") or "").strip().lower()


def _endpoint_matches(entry: dict[str, Any], target: str) -> bool:
    target = target.lower()
    key = _endpoint_key_value(entry)
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


def _endpoint_status_path_masses(entry: dict[str, Any]) -> dict[str, float]:
    status_masses = entry.get("status_path_masses") if isinstance(entry.get("status_path_masses"), dict) else {}
    parsed = {
        str(status): float(value)
        for status, value in status_masses.items()
        if _float_or_none(value) is not None and float(value) > 0
    }
    if parsed:
        return parsed
    path_mass = _float_or_none(entry.get("path_mass")) or 0.0
    status = str(entry.get("status") or "").strip().lower()
    if not status:
        realized = entry.get("realized")
        if realized is True:
            status = "realized"
        elif realized is False:
            status = "eliminated"
    return {status: path_mass} if status and path_mass > 0 else {}


def _binary_candidate_unresolved_mass(status_masses_by_row: list[dict[str, float]]) -> float:
    if not status_masses_by_row:
        return 0.0
    return min(
        1.0,
        max(
            (
                float(status_masses.get("unresolved") or 0.0)
                + float(status_masses.get("insufficient_ticks") or 0.0)
            )
            for status_masses in status_masses_by_row
        ),
    )


def extract_worldfork_forecast(
    case_id: str,
    condition: str,
    path_mass_payload: dict[str, Any],
    *,
    candidate_endpoint_keys: list[str] | None = None,
) -> dict[str, Any]:
    rows = path_mass_payload.get("endpoint_path_mass_distribution") or []
    if not isinstance(rows, list):
        rows = []
    normalized_candidate_keys = [
        str(key).strip().lower()
        for key in (candidate_endpoint_keys if candidate_endpoint_keys is not None else candidate_endpoint_keys_for_case(case_id))
        if str(key).strip()
    ]
    if normalized_candidate_keys:
        candidate_key_set = set(normalized_candidate_keys)
        rows = [
            entry
            for entry in rows
            if isinstance(entry, dict) and _endpoint_key_value(entry) in candidate_key_set
        ]
    yes_mass = 0.0
    no_mass = 0.0
    status_masses_by_candidate_row: list[dict[str, float]] = []
    matched_rows = 0
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        status_masses = _endpoint_status_path_masses(entry)
        if not status_masses:
            continue
        if _endpoint_matches(entry, "yes"):
            yes_mass += float(status_masses.get("realized") or 0.0)
            no_mass += float(status_masses.get("eliminated") or 0.0)
            status_masses_by_candidate_row.append(status_masses)
            matched_rows += 1
        elif _endpoint_matches(entry, "no"):
            no_mass += float(status_masses.get("realized") or 0.0)
            yes_mass += float(status_masses.get("eliminated") or 0.0)
            status_masses_by_candidate_row.append(status_masses)
            matched_rows += 1
    unresolved = _binary_candidate_unresolved_mass(status_masses_by_candidate_row)
    denom = yes_mass + no_mass + unresolved
    if denom > 0:
        p_yes = (yes_mass + 0.5 * unresolved) / denom
        p_no = 1.0 - p_yes
    else:
        p_yes = 0.5
        p_no = 0.5
    return {
        "case_id": case_id,
        "condition": condition,
        "p_yes": round(p_yes, 10),
        "p_no": round(p_no, 10),
        "unresolved_mass": unresolved,
        "yes_realized_mass": round(yes_mass, 10),
        "no_realized_mass": round(no_mass, 10),
        "binary_status_mass_total": round(denom, 10),
        "forecast_distribution": {"yes": p_yes, "no": p_no, "unresolved": unresolved},
        "extraction_note": (
            "derived_from_candidate_endpoint_path_mass_distribution"
            if normalized_candidate_keys
            else "derived_from_endpoint_path_mass_distribution"
        ),
        "mass_extraction_method": "binary_candidate_status_path_masses_with_unresolved_split",
        "matched_endpoint_rows": matched_rows,
        "candidate_endpoint_keys": normalized_candidate_keys,
    }


def _discover_worldfork_short_run_dirs(
    run_root: Path,
    *,
    input_prefix: Path,
    case_filter: set[str] | None,
    condition_filter: set[str] | None,
    case_limit: int | None,
) -> list[dict[str, Any]]:
    manifest_rows: dict[str, dict[str, Any]] = {}
    manifest_path = run_root / "manifests/worldfork_short_manifest.jsonl"
    if manifest_path.exists():
        for row in read_jsonl(manifest_path):
            run_dir = str(row.get("run_dir") or "")
            if run_dir:
                manifest_rows[run_dir] = row

    base_dir = input_prefix if input_prefix.is_absolute() else run_root / input_prefix
    if not base_dir.exists():
        raise SystemExit(f"missing input-prefix: {base_dir}")

    rows: list[dict[str, Any]] = []
    seen_big_bangs: set[str] = set()
    for condition_dir in sorted(path for path in base_dir.iterdir() if path.is_dir()):
        condition = condition_dir.name
        if condition_filter and condition not in condition_filter:
            continue
        for case_dir in sorted(path for path in condition_dir.iterdir() if path.is_dir()):
            case_id = case_dir.name
            if case_filter and case_id not in case_filter:
                continue
            big_bang_id = _read_id_file(case_dir / "big_bang_id.txt")
            if not big_bang_id or big_bang_id in seen_big_bangs:
                continue
            seen_big_bangs.add(big_bang_id)
            try:
                relative_dir = case_dir.relative_to(run_root)
            except ValueError:
                relative_dir = case_dir
            manifest_row = manifest_rows.get(str(relative_dir), {})
            rows.append(
                {
                    "case_id": case_id,
                    "condition": condition,
                    "big_bang_id": big_bang_id,
                    "run_dir": relative_dir,
                    "init_job_id": manifest_row.get("init_job_id") or _read_id_file(case_dir / "init_job_id.txt"),
                    "run_job_id": manifest_row.get("run_job_id") or _read_id_file(case_dir / "run_job_id.txt"),
                    "source_status": manifest_row.get("status") or "artifact_discovered",
                    "source_route_policy_id": manifest_row.get("route_policy_id"),
                    "source_max_ticks_requested": manifest_row.get("max_ticks_requested"),
                    "source_tick_duration_minutes": manifest_row.get("tick_duration_minutes"),
                }
            )
            if case_limit is not None and len(rows) >= case_limit:
                return rows
    return rows


def posthoc_reevaluate_worldfork_short_ledgers(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    output = _prediction_output_path(run_root, args.prediction_output)
    predictions = _prediction_rows_by_key(output, route_policy_id=args.route_policy_id)
    case_filter = {item.strip() for item in args.case_ids.split(",") if item.strip()} if args.case_ids else None
    condition_filter = {item.strip() for item in args.conditions.split(",") if item.strip()} if args.conditions else None
    rows = _discover_worldfork_short_run_dirs(
        run_root,
        input_prefix=args.input_prefix,
        case_filter=case_filter,
        condition_filter=condition_filter,
        case_limit=args.case_limit,
    )
    if not rows:
        raise SystemExit("no existing WorldFork short run directories matched the filters")

    for row in rows:
        case_id = str(row["case_id"])
        condition = str(row["condition"])
        key = (case_id, condition, args.route_policy_id or "")
        if key in predictions and not args.force:
            print(json.dumps({"case_id": case_id, "condition": condition, "status": "skipped_existing_posthoc"}))
            continue

        out_dir = run_root / row["run_dir"]
        big_bang_id = str(row["big_bang_id"])
        multiverse_artifact_dir = out_dir / "posthoc_multiverse_endpoint_ledgers"
        try:
            multiverses = client.request("GET", f"/big-bangs/{big_bang_id}/multiverses")
        except urllib.error.HTTPError as exc:
            payload = {
                "ok": False,
                "error": {
                    "type": "http_error",
                    "status": exc.code,
                    "reason": exc.reason,
                    "big_bang_id": big_bang_id,
                },
            }
            _write_json(multiverse_artifact_dir / "multiverses.json", payload)
            print(json.dumps({"case_id": case_id, "condition": condition, "status": "multiverse_list_failed"}))
            continue

        _write_json(multiverse_artifact_dir / "multiverses.json", multiverses)
        multiverse_rows = _artifact_list(multiverses)
        evaluated: list[dict[str, Any]] = []
        if not args.skip_multiverse_reevaluation:
            candidate_endpoints = _candidate_endpoints_for_case(case_id) if args.inject_candidate_endpoints else []
            for multiverse in multiverse_rows:
                if not isinstance(multiverse, dict) or not multiverse.get("id"):
                    continue
                if candidate_endpoints:
                    for endpoint in candidate_endpoints:
                        evaluated.append(
                            _evaluate_multiverse_endpoint_ledger(
                                client,
                                out_dir,
                                multiverse,
                                candidate_endpoint=endpoint,
                            )
                        )
                else:
                    evaluated.append(_evaluate_multiverse_endpoint_ledger(client, out_dir, multiverse))
        _write_json(multiverse_artifact_dir / "evaluate_all.json", evaluated)

        _capture_run_artifacts(client, out_dir, big_bang_id)
        path_mass = _artifact_dict(_read_json_artifact(out_dir / "path_mass.json"))
        prediction = extract_worldfork_forecast(
            case_id,
            condition,
            path_mass,
            candidate_endpoint_keys=candidate_endpoint_keys_for_case(case_id),
        )
        prediction["route_policy_id"] = args.route_policy_id
        prediction["source_route_policy_id"] = row.get("source_route_policy_id")
        prediction["source_run_status"] = row.get("source_status")
        prediction["source_max_ticks_requested"] = row.get("source_max_ticks_requested")
        prediction["max_ticks_requested"] = args.max_ticks or row.get("source_max_ticks_requested")
        prediction["tick_duration_minutes"] = args.tick_duration_minutes or row.get("source_tick_duration_minutes")
        prediction["big_bang_id"] = big_bang_id
        prediction["posthoc_reevaluation"] = (
            "big_bang_only"
            if args.skip_multiverse_reevaluation
            else "multiverse_endpoint_ledgers_then_big_bang_path_mass"
        )
        prediction["evaluated_multiverse_count"] = len(evaluated)
        prediction["prediction_output"] = _display_run_path(output, run_root)
        append_jsonl(output, prediction)
        predictions[key] = prediction
        print(
            json.dumps(
                {
                    "case_id": case_id,
                    "condition": condition,
                    "status": "posthoc_refreshed",
                    "matched_endpoint_rows": prediction["matched_endpoint_rows"],
                    "unresolved_mass": prediction["unresolved_mass"],
                }
            )
        )


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


def worldfork_long_manifest_row(
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
    max_ticks_requested: int,
    max_total_ticks_requested: int,
    tick_duration_minutes: int,
    route_policy_id: str | None = None,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "condition": condition,
        "route_policy_id": route_policy_id,
        "max_ticks_requested": max_ticks_requested,
        "max_total_ticks_requested": max_total_ticks_requested,
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
        "notes": "E4 long-horizon audit row; score with audit/social rubrics, not resolved-card Brier.",
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
            runtime_context = resolved_forecast_runtime_context(
                case_id=case_id,
                max_ticks=args.max_ticks,
                base_tick_duration_minutes=args.tick_duration_minutes,
                deadline_aware=getattr(args, "deadline_aware_ticks", True),
            )
            tick_duration_minutes = int(runtime_context["tick_duration_minutes"])
            payload = build_init_job_payload(
                case_id=case_id,
                case_file=case_file,
                name_prefix=f"{args.name_prefix}_{condition}",
                max_ticks=args.max_ticks,
                tick_duration_minutes=tick_duration_minutes,
                branch_policy=WORLDFORK_SHORT_POLICIES[condition],
                forecast_context=runtime_context,
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
            if not getattr(args, "generate_reports", False):
                run_payload["skip_reports"] = True
            if runtime_context.get("endpoint_resolution_keys"):
                run_payload["endpoint_resolution_keys"] = runtime_context["endpoint_resolution_keys"]
            if getattr(args, "stop_when_endpoint_ledger_resolved", False):
                run_payload["stop_when_endpoint_ledger_resolved"] = True
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
            prediction = _annotate_prediction(
                extract_worldfork_forecast(
                    case_id,
                    condition,
                    path_mass,
                    candidate_endpoint_keys=runtime_context.get("endpoint_resolution_keys"),
                ),
                args,
            )
            prediction["tick_duration_minutes"] = tick_duration_minutes
            prediction["forecast_metadata"] = runtime_context.get("forecast_metadata")
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
                    tick_duration_minutes=tick_duration_minutes,
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
        runtime_context = resolved_forecast_runtime_context(
            case_id=case_id,
            max_ticks=args.max_ticks,
            base_tick_duration_minutes=args.tick_duration_minutes,
            deadline_aware=getattr(args, "deadline_aware_ticks", True),
        )
        tick_duration_minutes = int(runtime_context["tick_duration_minutes"])
        payload = build_init_job_payload(
            case_id=case_id,
            case_file=case_file,
            name_prefix=f"{args.name_prefix}_{condition}",
            max_ticks=args.max_ticks,
            tick_duration_minutes=tick_duration_minutes,
            branch_policy=WORLDFORK_SHORT_POLICIES[condition],
            forecast_context=runtime_context,
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
            "runtime_context": runtime_context,
            "tick_duration_minutes": tick_duration_minutes,
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
                    tick_duration_minutes=int(info.get("tick_duration_minutes") or args.tick_duration_minutes),
                    route_policy_id=args.route_policy_id,
                    prediction_output=_display_run_path(_prediction_output_path(run_root, args.prediction_output), run_root),
                ),
            )
            continue
        big_bang_id = str((job.get("result") or {}).get("big_bang_id"))
        (out_dir / "big_bang_id.txt").write_text(big_bang_id + "\n", encoding="utf-8")
        _capture_init_artifacts(client, out_dir, big_bang_id)

        run_payload = {"max_total_ticks": args.max_ticks}
        if not getattr(args, "generate_reports", False):
            run_payload["skip_reports"] = True
        runtime_context = info.get("runtime_context") if isinstance(info.get("runtime_context"), dict) else {}
        if runtime_context.get("endpoint_resolution_keys"):
            run_payload["endpoint_resolution_keys"] = runtime_context["endpoint_resolution_keys"]
        if getattr(args, "stop_when_endpoint_ledger_resolved", False):
            run_payload["stop_when_endpoint_ledger_resolved"] = True
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
            runtime_context = info.get("runtime_context") if isinstance(info.get("runtime_context"), dict) else {}
            prediction = _annotate_prediction(
                extract_worldfork_forecast(
                    case_id,
                    condition,
                    path_mass,
                    candidate_endpoint_keys=runtime_context.get("endpoint_resolution_keys"),
                ),
                args,
            )
            prediction["tick_duration_minutes"] = int(info.get("tick_duration_minutes") or args.tick_duration_minutes)
            prediction["forecast_metadata"] = runtime_context.get("forecast_metadata")
            append_jsonl(output, prediction)
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
                tick_duration_minutes=int(info.get("tick_duration_minutes") or args.tick_duration_minutes),
                route_policy_id=args.route_policy_id,
                prediction_output=_display_run_path(output, run_root),
            ),
        )
        print(json.dumps({"case_id": case_id, "condition": condition, "status": status, "ticks_run": result_payload.get("ticks_run")}))


def collect_worldfork_short_existing(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    output = _prediction_output_path(run_root, args.prediction_output)
    manifest = run_root / "manifests/worldfork_short_manifest.jsonl"
    predictions = _prediction_rows_by_key(output, route_policy_id=args.route_policy_id)
    manifested_run_job_statuses = _manifest_run_job_statuses(manifest)

    matrix = json.loads(RUN_MATRIX.read_text(encoding="utf-8"))
    default_ids = matrix["case_groups"]["worldfork_resolved_core12_fallback" if args.core12 else "resolved_24"]
    case_ids = _case_ids_from_manifest(run_root, args.case_ids or ",".join(default_ids), args.case_limit)
    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    for case_id in case_ids:
        for condition in conditions:
            if condition not in WORLDFORK_SHORT_POLICIES:
                raise SystemExit(f"unknown E3 condition: {condition}")
            relative_dir = Path(args.output_prefix) / condition / case_id
            out_dir = run_root / relative_dir
            resume_source = _artifact_dict(_read_json_artifact(out_dir / "resume_source.json"))
            big_bang_id = _read_id_file(out_dir / "big_bang_id.txt")
            run_job_id = _read_id_file(out_dir / "run_job_id.txt")
            init_job_id = _read_id_file(out_dir / "init_job_id.txt")
            if not big_bang_id:
                big_bang_id = str(resume_source.get("big_bang_id") or "")
            if not init_job_id:
                init_job_id = str(resume_source.get("init_job_id") or "")
            if not big_bang_id or not run_job_id:
                print(json.dumps({"case_id": case_id, "condition": condition, "status": "missing_existing_run_artifacts"}))
                continue

            job = client.request("GET", f"/jobs/{run_job_id}")
            _write_json(out_dir / "run_job_status_latest.json", job)
            if not _job_finished(job):
                print(json.dumps({"case_id": case_id, "condition": condition, "run_job_id": run_job_id, "status": job.get("status")}))
                continue
            _write_json(out_dir / "run_job_wait.json", {"ok": True, "data": job, "meta": {"terminal": True, "collected_existing": True}})

            result_payload = job.get("result") or {}
            status = "completed" if job.get("status") == "succeeded" else str(job.get("status"))
            if job.get("status") == "succeeded":
                _capture_run_artifacts(client, out_dir, big_bang_id)
                path_mass = json.loads((out_dir / "path_mass.json").read_text(encoding="utf-8"))
                key = (case_id, condition, args.route_policy_id or "")
                if key not in predictions or args.force:
                    prediction = _annotate_prediction(extract_worldfork_forecast(case_id, condition, path_mass), args)
                    append_jsonl(output, prediction)
                    predictions[key] = prediction

            previous_manifest_status = manifested_run_job_statuses.get(run_job_id)
            should_append_manifest = (
                args.force
                or previous_manifest_status is None
                or (status == "completed" and previous_manifest_status != "completed")
            )
            if should_append_manifest:
                append_jsonl(
                    manifest,
                    worldfork_short_manifest_row(
                        case_id=case_id,
                        condition=condition,
                        big_bang_id=big_bang_id,
                        init_job_id=init_job_id,
                        run_job_id=run_job_id,
                        status=status,
                        init_wait_seconds=_artifact_wait_seconds(out_dir / "init_job_wait.json"),
                        run_wait_seconds=_job_wall_seconds(job),
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
                manifested_run_job_statuses[run_job_id] = status
            print(json.dumps({"case_id": case_id, "condition": condition, "status": status, "ticks_run": result_payload.get("ticks_run")}))


def _worldfork_long_targets(args: argparse.Namespace, run_root: Path) -> list[dict[str, Any]]:
    matrix = json.loads(RUN_MATRIX.read_text(encoding="utf-8"))
    default_group = "minimum_long_horizon_6" if args.minimum6 else "long_horizon_18"
    default_ids = matrix["case_groups"][default_group]
    case_ids = _case_ids_from_manifest(run_root, args.case_ids or ",".join(default_ids), args.case_limit)
    conditions = [item.strip() for item in args.conditions.split(",") if item.strip()]
    completed = set()
    manifest = run_root / "manifests/worldfork_long_horizon_manifest.jsonl"
    if manifest.exists() and not args.force:
        for row in read_jsonl(manifest):
            if row.get("status") == "completed":
                completed.add(
                    (
                        str(row.get("case_id") or ""),
                        str(row.get("condition") or ""),
                        str(row.get("route_policy_id") or ""),
                    )
                )
    targets = []
    for case_id in case_ids:
        for condition in conditions:
            if condition not in WORLDFORK_LONG_POLICIES:
                raise SystemExit(f"unknown E4 condition: {condition}")
            key = (case_id, condition, args.route_policy_id or "")
            if key in completed and not args.force:
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


def run_worldfork_long_batch(args: argparse.Namespace) -> None:
    run_root = make_run_root(args.run_root)
    client = ApiClient(args.base_url, api_prefix=args.api_prefix, timeout=args.timeout)
    targets = _worldfork_long_targets(args, run_root)
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
            branch_policy=WORLDFORK_LONG_POLICIES[condition],
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

    _write_json(run_root / "setup/worldfork_long_batch_queues_after_init_submit.json", client.request("GET", "/jobs/queues"))
    init_completed = _wait_many_jobs(
        client,
        init_pending,
        artifact_prefix="init_job",
        wait_timeout=args.wait_timeout,
        poll_seconds=args.poll_seconds,
    )

    manifest = run_root / "manifests/worldfork_long_horizon_manifest.jsonl"
    run_pending: dict[str, dict[str, Any]] = {}
    for info in init_completed:
        job = info["job"]
        case_id = info["case_id"]
        condition = info["condition"]
        out_dir = info["out_dir"]
        if job.get("status") != "succeeded":
            append_jsonl(
                manifest,
                worldfork_long_manifest_row(
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
                    max_total_ticks_requested=args.max_total_ticks,
                    tick_duration_minutes=args.tick_duration_minutes,
                    route_policy_id=args.route_policy_id,
                ),
            )
            continue
        big_bang_id = str((job.get("result") or {}).get("big_bang_id"))
        (out_dir / "big_bang_id.txt").write_text(big_bang_id + "\n", encoding="utf-8")
        _capture_init_artifacts(client, out_dir, big_bang_id)

        run_payload = {"max_total_ticks": args.max_total_ticks}
        if getattr(args, "stop_when_endpoint_ledger_resolved", False):
            run_payload["stop_when_endpoint_ledger_resolved"] = True
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
    _write_json(run_root / "setup/worldfork_long_batch_queues_after_run_submit.json", client.request("GET", "/jobs/queues"))
    run_completed = _wait_many_jobs(
        client,
        run_pending,
        artifact_prefix="run_job",
        wait_timeout=args.wait_timeout,
        poll_seconds=args.poll_seconds,
    )

    for info in run_completed:
        job = info["job"]
        case_id = info["case_id"]
        condition = info["condition"]
        out_dir = info["out_dir"]
        result_payload = job.get("result") or {}
        status = "completed" if job.get("status") == "succeeded" else str(job.get("status"))
        if job.get("status") == "succeeded":
            _capture_run_artifacts(client, out_dir, info["big_bang_id"])
        append_jsonl(
            manifest,
            worldfork_long_manifest_row(
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
                max_total_ticks_requested=args.max_total_ticks,
                tick_duration_minutes=args.tick_duration_minutes,
                route_policy_id=args.route_policy_id,
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
    if card.get("as_of_date") or card.get("forecast_horizon"):
        parts.extend(["", "## Forecast Clock"])
        if card.get("as_of_date"):
            parts.append(f"As-of date: {card['as_of_date']}")
        if card.get("forecast_horizon"):
            parts.append(f"Forecast horizon: {card['forecast_horizon']}")
        deadline = _public_forecast_deadline(card)
        if deadline:
            parts.append(f"Forecast deadline date: {deadline}")
        parts.append("Treat the simulated clock as beginning at the as-of date.")
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
    if role == "resolved_forecast":
        parts.extend(
            [
                "",
                "## Binary forecast contract",
                "The explicit candidate endpoints are the primary scoring endpoints.",
                "Resolve yes only when the event occurs by the stated deadline.",
                "Resolve no when the stated deadline or public settlement point passes without the event occurring.",
                "Auxiliary mechanism endpoints must not keep the binary forecast unresolved once the yes/no endpoint is settled.",
                "Use auxiliary mechanism endpoints only as diagnostic support for the binary forecast.",
                "",
                "## Forecast simulation guidance",
                "Treat source-packet statements from the relevant authority, company, organizer, regulator, or official schedule as the baseline prior for future ticks.",
                "Generic risk notes identify possible slip mechanisms; they are not evidence that the slip, blockage, denial, or miss has already happened.",
                "At the deadline or settlement tick, make a best-effort yes/no path outcome consistent with the strongest source-packet evidence and simulated events.",
                "Resolve no only when simulated events show an authoritative miss, delay beyond the deadline, cancellation, denial, or continued non-availability; absence of independent proof inside the simulation is not enough by itself.",
                "A preorder-only note prevents premature yes before customer availability; it does not negate a later scheduled availability event.",
            ]
        )
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


def _prediction_p_yes(row: dict[str, Any]) -> float:
    return float(row.get("p_yes", row.get("forecast_distribution", {}).get("yes", 0.5)))


def _prediction_unresolved_mass(row: dict[str, Any]) -> float:
    return float(row.get("unresolved_mass", row.get("forecast_distribution", {}).get("unresolved", 0.0)))


def _blend_metric_row(
    *,
    direct_condition: str,
    worldfork_condition: str,
    alpha: float,
    case_ids: list[str],
    labels: dict[str, float],
    worldfork_predictions: dict[str, dict[str, Any]],
    direct_predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    brier_scores: list[float] = []
    log_scores: list[float] = []
    unresolved_masses: list[float] = []
    for case_id in case_ids:
        y = labels[case_id]
        direct_p = _prediction_p_yes(direct_predictions[case_id])
        worldfork_p = _prediction_p_yes(worldfork_predictions[case_id])
        p_yes = alpha * direct_p + (1.0 - alpha) * worldfork_p
        p_true = p_yes if y == 1.0 else 1.0 - p_yes
        direct_unresolved = _prediction_unresolved_mass(direct_predictions[case_id])
        worldfork_unresolved = _prediction_unresolved_mass(worldfork_predictions[case_id])
        unresolved = alpha * direct_unresolved + (1.0 - alpha) * worldfork_unresolved
        brier_scores.append((p_yes - y) ** 2)
        log_scores.append(-math.log(clamp(p_true)))
        unresolved_masses.append(unresolved)
    return {
        "direct_condition": direct_condition,
        "worldfork_condition": worldfork_condition,
        "alpha": alpha,
        "n": len(case_ids),
        "mean_brier": statistics.fmean(brier_scores),
        "mean_log_score": statistics.fmean(log_scores),
        "mean_unresolved_mass": statistics.fmean(unresolved_masses),
    }


def compute_direct_prior_blend_scores(
    *,
    labels: dict[str, float],
    worldfork_predictions: dict[str, dict[str, Any]],
    direct_predictions_by_condition: dict[str, dict[str, dict[str, Any]]],
    alphas: list[float],
    worldfork_condition: str,
) -> dict[str, list[dict[str, Any]]]:
    grid_rows: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    alpha_grid = sorted({round(float(alpha), 10) for alpha in alphas if 0.0 <= float(alpha) <= 1.0})
    if not alpha_grid:
        raise ValueError("at least one alpha in [0, 1] is required")

    for direct_condition, direct_predictions in sorted(direct_predictions_by_condition.items()):
        case_ids = sorted(set(labels) & set(worldfork_predictions) & set(direct_predictions))
        if not case_ids:
            continue
        condition_grid: list[dict[str, Any]] = []
        for alpha in alpha_grid:
            row = _blend_metric_row(
                direct_condition=direct_condition,
                worldfork_condition=worldfork_condition,
                alpha=alpha,
                case_ids=case_ids,
                labels=labels,
                worldfork_predictions=worldfork_predictions,
                direct_predictions=direct_predictions,
            )
            condition_grid.append(row)
            grid_rows.append(row)

        best_brier = min(condition_grid, key=lambda row: (row["mean_brier"], row["mean_log_score"], row["alpha"]))
        best_log = min(condition_grid, key=lambda row: (row["mean_log_score"], row["mean_brier"], row["alpha"]))

        def selection(selection_name: str, row: dict[str, Any], note: str) -> dict[str, Any]:
            return {
                **row,
                "selection": selection_name,
                "selected_alpha_mean": row["alpha"],
                "selected_alpha_min": row["alpha"],
                "selected_alpha_max": row["alpha"],
                "note": note,
            }

        selection_rows.extend(
            [
                selection(
                    "worldfork_only",
                    _blend_metric_row(
                        direct_condition=direct_condition,
                        worldfork_condition=worldfork_condition,
                        alpha=0.0,
                        case_ids=case_ids,
                        labels=labels,
                        worldfork_predictions=worldfork_predictions,
                        direct_predictions=direct_predictions,
                    ),
                    "E3 branching path-mass aggregate without a direct-call prior",
                ),
                selection(
                    "equal_blend",
                    _blend_metric_row(
                        direct_condition=direct_condition,
                        worldfork_condition=worldfork_condition,
                        alpha=0.5,
                        case_ids=case_ids,
                        labels=labels,
                        worldfork_predictions=worldfork_predictions,
                        direct_predictions=direct_predictions,
                    ),
                    "50/50 direct-call prior and E3 branching path-mass aggregate",
                ),
                selection(
                    "direct_only",
                    _blend_metric_row(
                        direct_condition=direct_condition,
                        worldfork_condition=worldfork_condition,
                        alpha=1.0,
                        case_ids=case_ids,
                        labels=labels,
                        worldfork_predictions=worldfork_predictions,
                        direct_predictions=direct_predictions,
                    ),
                    "Direct-call forecast without WorldFork path-mass adjustment",
                ),
                selection("best_brier_in_sample", best_brier, "Alpha selected on the same scored cases by mean Brier"),
                selection("best_log_in_sample", best_log, "Alpha selected on the same scored cases by mean log score"),
            ]
        )

        if len(case_ids) >= 2:
            heldout_brier: list[float] = []
            heldout_log: list[float] = []
            heldout_unresolved: list[float] = []
            selected_alphas: list[float] = []
            for heldout_case_id in case_ids:
                train_case_ids = [case_id for case_id in case_ids if case_id != heldout_case_id]
                train_rows = [
                    _blend_metric_row(
                        direct_condition=direct_condition,
                        worldfork_condition=worldfork_condition,
                        alpha=alpha,
                        case_ids=train_case_ids,
                        labels=labels,
                        worldfork_predictions=worldfork_predictions,
                        direct_predictions=direct_predictions,
                    )
                    for alpha in alpha_grid
                ]
                selected = min(train_rows, key=lambda row: (row["mean_brier"], row["mean_log_score"], row["alpha"]))
                selected_alpha = float(selected["alpha"])
                selected_alphas.append(selected_alpha)
                heldout_row = _blend_metric_row(
                    direct_condition=direct_condition,
                    worldfork_condition=worldfork_condition,
                    alpha=selected_alpha,
                    case_ids=[heldout_case_id],
                    labels=labels,
                    worldfork_predictions=worldfork_predictions,
                    direct_predictions=direct_predictions,
                )
                heldout_brier.append(float(heldout_row["mean_brier"]))
                heldout_log.append(float(heldout_row["mean_log_score"]))
                heldout_unresolved.append(float(heldout_row["mean_unresolved_mass"]))

            selection_rows.append(
                {
                    "direct_condition": direct_condition,
                    "worldfork_condition": worldfork_condition,
                    "selection": "leave_one_out_brier_tuned",
                    "alpha": statistics.fmean(selected_alphas),
                    "selected_alpha_mean": statistics.fmean(selected_alphas),
                    "selected_alpha_min": min(selected_alphas),
                    "selected_alpha_max": max(selected_alphas),
                    "n": len(case_ids),
                    "mean_brier": statistics.fmean(heldout_brier),
                    "mean_log_score": statistics.fmean(heldout_log),
                    "mean_unresolved_mass": statistics.fmean(heldout_unresolved),
                    "note": "Each held-out case scored with alpha selected by mean Brier on the other cases",
                }
            )

    return {"grid_rows": grid_rows, "selection_rows": selection_rows}


def _alpha_grid(step: float) -> list[float]:
    if step <= 0.0 or step > 1.0:
        raise SystemExit("--alpha-step must be in (0, 1]")
    count = int(round(1.0 / step))
    values = [round(index * step, 10) for index in range(count + 1)]
    if values[-1] != 1.0:
        values.append(1.0)
    return sorted(set(values))


def _read_prediction_map(path: Path, *, condition: str | None = None) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        row_condition = str(row.get("condition") or "")
        if condition and row_condition != condition:
            continue
        case_id = str(row.get("case_id") or "")
        if not case_id:
            continue
        predictions[case_id] = row
    return predictions


def _resolve_run_relative_path(run_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    run_relative = run_root / path
    if run_relative.exists():
        return run_relative
    return path


def _format_float(value: Any) -> str:
    return f"{float(value):.6f}"


def _write_blend_csv(path: Path, rows: list[dict[str, Any]], *, include_selection: bool) -> None:
    fieldnames = [
        "direct_condition",
        "worldfork_condition",
        "alpha",
        "n",
        "mean_brier",
        "mean_log_score",
        "mean_unresolved_mass",
    ]
    if include_selection:
        fieldnames = [
            "direct_condition",
            "worldfork_condition",
            "selection",
            "alpha",
            "selected_alpha_mean",
            "selected_alpha_min",
            "selected_alpha_max",
            "n",
            "mean_brier",
            "mean_log_score",
            "mean_unresolved_mass",
            "note",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            for key in ["alpha", "selected_alpha_mean", "selected_alpha_min", "selected_alpha_max", "mean_brier", "mean_log_score", "mean_unresolved_mass"]:
                if key in formatted:
                    formatted[key] = _format_float(formatted[key])
            writer.writerow({key: formatted.get(key, "") for key in fieldnames})


def score_e3_direct_prior_blends(args: argparse.Namespace) -> None:
    run_root = args.run_root
    worldfork_path = _resolve_run_relative_path(run_root, args.worldfork_predictions)
    worldfork_predictions = _read_prediction_map(worldfork_path, condition=args.worldfork_condition)
    if not worldfork_predictions:
        raise SystemExit(f"no WorldFork predictions found for {args.worldfork_condition}: {worldfork_path}")

    direct_predictions_by_condition: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for input_path in args.direct_predictions:
        path = _resolve_run_relative_path(run_root, input_path)
        for row in read_jsonl(path):
            condition = str(row.get("condition") or "")
            case_id = str(row.get("case_id") or "")
            if not condition or not case_id:
                continue
            direct_predictions_by_condition[condition][case_id] = row

    labels = {
        row["case_id"]: 1.0 if row["resolution"] == "yes" else 0.0
        for row in read_jsonl(PRIVATE_36)
        if row.get("resolution") in {"yes", "no"}
    }
    scores = compute_direct_prior_blend_scores(
        labels=labels,
        worldfork_predictions=worldfork_predictions,
        direct_predictions_by_condition=dict(direct_predictions_by_condition),
        alphas=_alpha_grid(args.alpha_step),
        worldfork_condition=args.worldfork_condition,
    )
    grid_output = args.grid_output if args.grid_output.is_absolute() else run_root / args.grid_output
    best_output = args.best_output if args.best_output.is_absolute() else run_root / args.best_output
    _write_blend_csv(grid_output, scores["grid_rows"], include_selection=False)
    _write_blend_csv(best_output, scores["selection_rows"], include_selection=True)
    print(
        json.dumps(
            {
                "worldfork_predictions": str(worldfork_path),
                "worldfork_condition": args.worldfork_condition,
                "grid_output": str(grid_output),
                "best_output": str(best_output),
                "grid_rows": len(scores["grid_rows"]),
                "selection_rows": len(scores["selection_rows"]),
                "best_brier_rows": [
                    row
                    for row in scores["selection_rows"]
                    if row.get("selection") in {"best_brier_in_sample", "leave_one_out_brier_tuned"}
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


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


def assemble_worldfork_latest_predictions(args: argparse.Namespace) -> None:
    rows = assemble_latest_prediction_rows(
        input_paths=args.predictions,
        route_policy_id=args.route_policy_id,
        condition=args.condition,
    )
    write_jsonl(args.output, rows)
    print(json.dumps({"output": str(args.output), "rows": len(rows)}, sort_keys=True))


def assemble_latest_prediction_rows(
    *,
    input_paths: list[Path],
    route_policy_id: str | None = None,
    condition: str | None = None,
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for source_index, path in enumerate(input_paths):
        if not path.exists():
            continue
        for row_index, row in enumerate(read_jsonl(path)):
            case_id = str(row.get("case_id") or "")
            row_condition = str(condition or row.get("condition") or "")
            if not case_id or not row_condition:
                continue
            key = (case_id, row_condition)
            if key not in latest:
                order.append(key)
            assembled = dict(row)
            assembled["condition"] = row_condition
            if route_policy_id:
                assembled["source_route_policy_id"] = assembled.get("route_policy_id")
                assembled["route_policy_id"] = route_policy_id
            assembled["assembled_from"] = str(path)
            assembled["assembled_input_index"] = source_index
            assembled["assembled_row_index"] = row_index
            assembled["assembly_note"] = "latest_input_wins_by_case_and_condition"
            latest[key] = assembled
    return [latest[key] for key in order if key in latest]


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

    blend = sub.add_parser(
        "score-e3-direct-prior-blends",
        help="Score existing E2 direct-call priors blended with the E3 branching path-mass aggregate.",
    )
    blend.add_argument("--run-root", type=Path, required=True)
    blend.add_argument(
        "--worldfork-predictions",
        type=Path,
        default=Path("raw/E3_worldfork_deadline_aware_branching_core12_posthoc_fixed/worldfork_predictions.jsonl"),
    )
    blend.add_argument("--worldfork-condition", default="worldfork_branching_short")
    blend.add_argument("--direct-predictions", type=Path, nargs="+", required=True)
    blend.add_argument("--alpha-step", type=float, default=0.05)
    blend.add_argument("--grid-output", type=Path, default=Path("results/e3_direct_prior_blend_alpha_grid.csv"))
    blend.add_argument("--best-output", type=Path, default=Path("results/e3_direct_prior_blend_best.csv"))
    blend.set_defaults(func=score_e3_direct_prior_blends)

    assemble = sub.add_parser(
        "assemble-worldfork-latest-predictions",
        help="Assemble one latest WorldFork prediction row per case/condition before scoring.",
    )
    assemble.add_argument("predictions", type=Path, nargs="+")
    assemble.add_argument("--output", type=Path, required=True)
    assemble.add_argument("--route-policy-id", help="Optional route-policy ID to stamp on assembled rows.")
    assemble.add_argument("--condition", help="Optional condition label to use for every assembled row.")
    assemble.set_defaults(func=assemble_worldfork_latest_predictions)

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
    short.add_argument("--max-ticks", type=int, default=8, help="Maximum tick cap, not a required stopping target.")
    short.add_argument("--tick-duration-minutes", type=int, default=720)
    short.add_argument("--no-deadline-aware-ticks", dest="deadline_aware_ticks", action="store_false")
    short.set_defaults(deadline_aware_ticks=True)
    short.add_argument("--stop-when-endpoint-ledger-resolved", action="store_true")
    short.add_argument("--generate-reports", action="store_true", help="Generate multiverse/final reports during benchmark run jobs.")
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
    short_batch.add_argument("--max-ticks", type=int, default=8, help="Maximum tick cap, not a required stopping target.")
    short_batch.add_argument("--tick-duration-minutes", type=int, default=720)
    short_batch.add_argument("--no-deadline-aware-ticks", dest="deadline_aware_ticks", action="store_false")
    short_batch.set_defaults(deadline_aware_ticks=True)
    short_batch.add_argument("--stop-when-endpoint-ledger-resolved", action="store_true")
    short_batch.add_argument("--generate-reports", action="store_true", help="Generate multiverse/final reports during benchmark run jobs.")
    short_batch.add_argument("--core12", action="store_true", help="Use the resolved core-12 fallback from the run matrix.")
    short_batch.add_argument("--force", action="store_true")
    short_batch.set_defaults(func=run_worldfork_short_batch)

    collect_short = sub.add_parser(
        "collect-worldfork-short-existing",
        help="Collect terminal E3 short run artifacts from existing run directories without initializing new Big Bangs.",
    )
    collect_short.add_argument("--run-root", type=Path, required=True)
    collect_short.add_argument("--base-url", default="http://127.0.0.1:8003")
    collect_short.add_argument("--api-prefix", default="/api")
    collect_short.add_argument("--timeout", type=float, default=60.0)
    collect_short.add_argument("--case-ids", help="Comma-separated case IDs. Defaults to resolved_24 or core12 fallback.")
    collect_short.add_argument("--case-limit", type=int)
    collect_short.add_argument("--conditions", default="worldfork_no_branch_short,worldfork_branching_short")
    collect_short.add_argument("--output-prefix", default="raw/E3_worldfork_short_batch")
    collect_short.add_argument("--prediction-output", default="raw/E3_worldfork_short/worldfork_predictions.jsonl")
    collect_short.add_argument("--route-policy-id", help="Optional route-policy label to stamp into predictions and manifest rows.")
    collect_short.add_argument("--max-ticks", type=int, default=8)
    collect_short.add_argument("--tick-duration-minutes", type=int, default=720)
    collect_short.add_argument("--core12", action="store_true", help="Use the resolved core-12 fallback from the run matrix.")
    collect_short.add_argument("--force", action="store_true")
    collect_short.set_defaults(func=collect_worldfork_short_existing)

    long_batch = sub.add_parser("run-worldfork-long-batch", help="Run queued E4 long-horizon WorldFork audit cases.")
    long_batch.add_argument("--run-root", type=Path, required=True)
    long_batch.add_argument("--base-url", default="http://127.0.0.1:8003")
    long_batch.add_argument("--api-prefix", default="/api")
    long_batch.add_argument("--timeout", type=float, default=60.0)
    long_batch.add_argument("--wait-timeout", type=float, default=86400.0)
    long_batch.add_argument("--poll-seconds", type=float, default=20.0)
    long_batch.add_argument("--case-ids", help="Comma-separated case IDs. Defaults to long_horizon_18 or minimum-6 fallback.")
    long_batch.add_argument("--case-limit", type=int)
    long_batch.add_argument("--conditions", default="worldfork_full_branching_long")
    long_batch.add_argument("--output-prefix", default="raw/E4_long_horizon")
    long_batch.add_argument("--route-policy-id", help="Optional route-policy label to stamp into manifest rows.")
    long_batch.add_argument("--name-prefix", default="E4_long_horizon")
    long_batch.add_argument("--max-ticks", type=int, default=35, help="Initializer tick cap, not a required stopping target.")
    long_batch.add_argument("--max-total-ticks", type=int, default=240, help="Runtime tick cap, not a required stopping target.")
    long_batch.add_argument("--tick-duration-minutes", type=int, default=720)
    long_batch.add_argument("--stop-when-endpoint-ledger-resolved", action="store_true")
    long_batch.add_argument("--minimum6", action="store_true", help="Use the minimum_long_horizon_6 fallback from the run matrix.")
    long_batch.add_argument("--force", action="store_true")
    long_batch.set_defaults(func=run_worldfork_long_batch)

    e4_artifacts = sub.add_parser(
        "generate-e4-paper-artifacts",
        help="Generate offline E4 long-horizon CSV/JSON/Markdown paper artifacts from terminal run directories.",
    )
    e4_artifacts.add_argument("--run-root", type=Path, required=True)
    e4_artifacts.add_argument("--input-prefix", type=Path, default=E4_DEFAULT_INPUT_PREFIX)
    e4_artifacts.add_argument("--manifest", type=Path, default=E4_LONG_HORIZON_MANIFEST)
    e4_artifacts.add_argument("--bootstrap-iterations", type=int, default=2000)
    e4_artifacts.add_argument("--bootstrap-seed", type=int, default=20260505)
    e4_artifacts.set_defaults(func=generate_e4_paper_artifacts)

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

    posthoc_short = sub.add_parser(
        "posthoc-reevaluate-worldfork-short-ledgers",
        help=(
            "Reuse existing E3 branch runs by re-evaluating multiverse endpoint ledgers, "
            "aggregating path mass, and writing separate posthoc prediction rows."
        ),
    )
    posthoc_short.add_argument("--run-root", type=Path, required=True)
    posthoc_short.add_argument("--base-url", default="http://127.0.0.1:8003")
    posthoc_short.add_argument("--api-prefix", default="/api")
    posthoc_short.add_argument("--timeout", type=float, default=360.0)
    posthoc_short.add_argument("--input-prefix", type=Path, required=True)
    posthoc_short.add_argument("--prediction-output", required=True)
    posthoc_short.add_argument("--route-policy-id", required=True)
    posthoc_short.add_argument("--case-ids", help="Optional comma-separated case IDs to reevaluate.")
    posthoc_short.add_argument("--case-limit", type=int)
    posthoc_short.add_argument("--conditions", help="Optional comma-separated E3 conditions to reevaluate.")
    posthoc_short.add_argument("--max-ticks", type=int)
    posthoc_short.add_argument("--tick-duration-minutes", type=int)
    posthoc_short.add_argument("--skip-multiverse-reevaluation", action="store_true")
    posthoc_short.add_argument(
        "--no-inject-candidate-endpoints",
        dest="inject_candidate_endpoints",
        action="store_false",
        help="Do not seed branch reevaluation with the public card's explicit candidate endpoints.",
    )
    posthoc_short.set_defaults(inject_candidate_endpoints=True)
    posthoc_short.add_argument("--force", action="store_true")
    posthoc_short.set_defaults(func=posthoc_reevaluate_worldfork_short_ledgers)

    resume_short = sub.add_parser(
        "resume-worldfork-short-batch",
        help="Resume existing E3 short WorldFork BigBangs to a higher tick cap without reinitializing.",
    )
    resume_short.add_argument("--run-root", type=Path, required=True)
    resume_short.add_argument("--base-url", default="http://127.0.0.1:8003")
    resume_short.add_argument("--api-prefix", default="/api")
    resume_short.add_argument("--timeout", type=float, default=60.0)
    resume_short.add_argument("--wait-timeout", type=float, default=3600.0)
    resume_short.add_argument("--poll-seconds", type=float, default=10.0)
    resume_short.add_argument("--case-ids", help="Optional comma-separated case IDs to resume.")
    resume_short.add_argument("--conditions", default="worldfork_no_branch_short")
    resume_short.add_argument("--source-prediction-output", required=True)
    resume_short.add_argument("--source-route-policy-id", required=True)
    resume_short.add_argument("--output-prefix", default="raw/E3_worldfork_short_resume")
    resume_short.add_argument("--prediction-output", required=True)
    resume_short.add_argument("--route-policy-id", required=True)
    resume_short.add_argument("--resume-attempt-id", help="Optional suffix for fresh job idempotency keys on retry.")
    resume_short.add_argument("--max-ticks", type=int, required=True, help="Maximum continuation tick cap, not a required stopping target.")
    resume_short.add_argument("--tick-duration-minutes", type=int, default=720)
    resume_short.add_argument("--stop-when-endpoint-ledger-resolved", action="store_true")
    resume_short.add_argument(
        "--skip-resolved-unresolved-mass",
        type=float,
        default=0.0,
        help="Carry forward source predictions with unresolved_mass at or below this threshold; use a negative value to disable.",
    )
    resume_short.add_argument("--force", action="store_true")
    resume_short.set_defaults(func=resume_worldfork_short_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
