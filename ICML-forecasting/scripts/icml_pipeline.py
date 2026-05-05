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
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "ICML-forecasting"
EXISTING_72 = ROOT / "skills/worldfork-full-agent-test/references/accuracy-benchmark-prompts.jsonl"
PUBLIC_36 = PACKAGE / "worldfork_additional_36_public.jsonl"
PRIVATE_36 = PACKAGE / "worldfork_additional_36_private_eval.jsonl"
LEGACY_36 = PACKAGE / "worldfork_additional_36_legacy_schema.jsonl"
RUN_MATRIX = PACKAGE / "AGENT_BENCHMARK_RUN_MATRIX.json"


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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
