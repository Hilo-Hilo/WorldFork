from __future__ import annotations

from typing import Any

FORECAST_PROBABILITY_SCHEMA_VERSION = "worldfork.forecast_probability.v1"
PRIMARY_ENDPOINT_ID = "primary_binary_outcome"
PREDICTION_METHOD = "path_mass_binary_endpoint_extraction"
UNCERTAIN_MASS_YES_SHARE = 0.5

_AUXILIARY_STATUSES = {"process_only"}
_UNCERTAIN_STATUSES = {"active", "weakened", "unresolved", "insufficient_ticks", "process_only"}


def extract_forecast_predictions(content: dict[str, Any]) -> dict[str, Any]:
    """Extract calibrated probability forecasts from report evidence.

    WorldFork endpoint ledgers track terminal-state predicates. This helper is
    the narrow probability boundary above those ledgers: it converts path-mass
    endpoint evidence into a Prophet-style p_yes value that downstream scoring
    can use without interpreting raw simulation mechanics.
    """

    primary = _extract_primary_forecast(content)
    return {
        "schema_version": FORECAST_PROBABILITY_SCHEMA_VERSION,
        "primary": primary,
        "predictions": [primary],
    }


def _extract_primary_forecast(content: dict[str, Any]) -> dict[str, Any]:
    predicate_forecast = _forecast_from_predicate_resolutions(content)
    if predicate_forecast is not None:
        return predicate_forecast
    rows = _source_rows(content)
    mass = _mass_from_rows(rows)
    total = mass["yes"] + mass["no"] + mass["uncertain"]
    if total <= 0:
        p_yes = 0.5
        confidence = 0.0
        resolution_state = "uncertain"
    else:
        p_yes = (mass["yes"] + UNCERTAIN_MASS_YES_SHARE * mass["uncertain"]) / total
        binary_mass = mass["yes"] + mass["no"]
        confidence = binary_mass / total
        if binary_mass <= 0:
            resolution_state = "uncertain"
        elif mass["uncertain"] <= 1e-12:
            resolution_state = "resolved"
        else:
            resolution_state = "inferred"

    excluded_auxiliary_keys = [
        row["endpoint_key"]
        for row in rows
        if row.get("endpoint_key") and str(row.get("status") or "").lower() in _AUXILIARY_STATUSES
    ]
    evidence_used = [
        {
            "endpoint_key": row.get("endpoint_key"),
            "label": row.get("label"),
            "status": row.get("status"),
            "realized": row.get("realized"),
            "path_mass": row.get("path_mass"),
            "status_path_masses": row.get("status_path_masses") or {},
        }
        for row in rows[:12]
    ]
    return {
        "endpoint_id": PRIMARY_ENDPOINT_ID,
        "question": content.get("scenario_question"),
        "p_yes": _round_probability(p_yes),
        "p_no": _round_probability(1.0 - p_yes),
        "confidence": _round_probability(confidence),
        "resolution_state": resolution_state,
        "method": PREDICTION_METHOD,
        "calibration": {
            "uncertain_mass_yes_share": UNCERTAIN_MASS_YES_SHARE,
            "unresolved_mass_policy": "split_evenly_between_yes_and_no",
        },
        "mass": {key: _round_probability(value) for key, value in mass.items()},
        "excluded_auxiliary_endpoint_keys": excluded_auxiliary_keys,
        "evidence_used": evidence_used,
        "rationale": _rationale(mass=mass, p_yes=p_yes, resolution_state=resolution_state),
    }


def _forecast_from_predicate_resolutions(content: dict[str, Any]) -> dict[str, Any] | None:
    predicates = content.get("predicate_resolutions")
    if not isinstance(predicates, list):
        return None
    for predicate in predicates:
        if not isinstance(predicate, dict):
            continue
        ptype = str(predicate.get("type") or "").lower()
        if ptype not in {"binary_event", "threshold_breach"}:
            continue
        total = _coerce_float(predicate.get("total_path_mass"), default=0.0)
        if total <= 0:
            continue
        yes = _coerce_float(predicate.get("fired_path_mass"), default=0.0)
        no = _coerce_float(predicate.get("false_path_mass"), default=0.0)
        uncertain = _coerce_float(predicate.get("null_path_mass"), default=max(0.0, total - yes - no))
        if "hit_rate" in predicate and "false_path_mass" not in predicate and "null_path_mass" not in predicate:
            p_yes = _coerce_float(predicate.get("hit_rate"), default=yes / total)
            no = max(0.0, total - yes)
            uncertain = 0.0
        else:
            p_yes = (yes + UNCERTAIN_MASS_YES_SHARE * uncertain) / total
        binary_mass = max(0.0, min(total, yes + no))
        confidence = binary_mass / total
        resolution_state = "resolved" if uncertain <= 1e-12 else "inferred"
        endpoint_id = str(predicate.get("predicate_id") or predicate.get("id") or PRIMARY_ENDPOINT_ID)
        return {
            "endpoint_id": endpoint_id,
            "question": content.get("scenario_question"),
            "description": predicate.get("description"),
            "p_yes": _round_probability(p_yes),
            "p_no": _round_probability(1.0 - p_yes),
            "confidence": _round_probability(confidence),
            "resolution_state": resolution_state,
            "method": "predicate_resolution_path_mass",
            "calibration": {
                "uncertain_mass_yes_share": UNCERTAIN_MASS_YES_SHARE,
                "unresolved_mass_policy": "split_evenly_between_yes_and_no",
            },
            "mass": {
                "yes": _round_probability(yes),
                "no": _round_probability(no),
                "uncertain": _round_probability(uncertain),
            },
            "excluded_auxiliary_endpoint_keys": [],
            "evidence_used": [
                {
                    "predicate_id": endpoint_id,
                    "type": predicate.get("type"),
                    "description": predicate.get("description"),
                    "fired_path_mass": predicate.get("fired_path_mass"),
                    "false_path_mass": predicate.get("false_path_mass"),
                    "null_path_mass": predicate.get("null_path_mass"),
                    "total_path_mass": predicate.get("total_path_mass"),
                    "hit_rate": predicate.get("hit_rate"),
                }
            ],
            "rationale": _rationale(
                mass={"yes": yes, "no": no, "uncertain": uncertain},
                p_yes=p_yes,
                resolution_state=resolution_state,
            ),
        }
    return None


def _source_rows(content: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = content.get("endpoint_ledger")
    ledger_payload = ledger.get("payload") if isinstance(ledger, dict) else {}
    payload = ledger_payload if isinstance(ledger_payload, dict) else {}
    rows = payload.get("endpoint_path_mass_distribution")
    if not isinstance(rows, list) or not rows:
        rows = content.get("endpoint_path_mass_distribution")
    if not isinstance(rows, list) or not rows:
        rows = content.get("endpoint_histogram")
    if not isinstance(rows, list) or not rows:
        rows = ledger.get("entries") if isinstance(ledger, dict) else []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _mass_from_rows(rows: list[dict[str, Any]]) -> dict[str, float]:
    mass = {"yes": 0.0, "no": 0.0, "uncertain": 0.0}
    if not rows:
        mass["uncertain"] = 1.0
        return mass
    realized_candidates: list[tuple[str, float]] = []
    default_weight = 1.0 / len(rows)
    for row in rows:
        endpoint_key = str(row.get("endpoint_key") or "")
        row_mass = _path_mass(row, default=default_weight)
        status_masses = row.get("status_path_masses")
        if isinstance(status_masses, dict) and status_masses:
            yes = _coerce_float(status_masses.get("realized"), default=0.0)
            no = _coerce_float(status_masses.get("eliminated"), default=0.0)
            known = yes + no
            uncertain = sum(
                _coerce_float(value, default=0.0)
                for key, value in status_masses.items()
                if str(key).lower() in _UNCERTAIN_STATUSES
            )
            missing = max(0.0, row_mass - known - uncertain)
            if yes > 0:
                realized_candidates.append((endpoint_key, yes))
            mass["no"] += no
            mass["uncertain"] += uncertain + missing
            continue

        realized = row.get("realized")
        status = str(row.get("status") or "").lower()
        if realized is True or status == "realized":
            realized_candidates.append((endpoint_key, row_mass))
        elif realized is False or status == "eliminated":
            mass["no"] += row_mass
        else:
            mass["uncertain"] += row_mass
    if len(realized_candidates) <= 1:
        mass["yes"] += sum(value for _endpoint_key, value in realized_candidates)
    else:
        winner = max(realized_candidates, key=lambda item: item[1])
        mass["yes"] += winner[1]
        mass["no"] += sum(value for endpoint_key, value in realized_candidates if endpoint_key != winner[0])
    return mass


def _path_mass(row: dict[str, Any], *, default: float) -> float:
    value = row.get("path_mass")
    if value is None:
        value = row.get("probability")
    return max(0.0, _coerce_float(value, default=default))


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _round_probability(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 10)


def _rationale(*, mass: dict[str, float], p_yes: float, resolution_state: str) -> str:
    return (
        f"Computed p_yes={_round_probability(p_yes)} from path-mass endpoint evidence: "
        f"yes={_round_probability(mass['yes'])}, no={_round_probability(mass['no'])}, "
        f"uncertain={_round_probability(mass['uncertain'])}. "
        f"Resolution state is {resolution_state}."
    )
