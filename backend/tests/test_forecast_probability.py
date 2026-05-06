from __future__ import annotations

from app.domains.report.probability import extract_forecast_predictions


def test_extracts_branch_mass_weighted_binary_probability() -> None:
    content = {
        "scenario_question": "Will the emergency policy survive court review?",
        "endpoint_ledger": {
            "payload": {
                "aggregation": "path_mass_by_endpoint_status",
                "path_probability_mass": 1.0,
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "policy_survives",
                        "label": "Policy survives",
                        "status": "realized",
                        "realized": True,
                        "path_mass": 0.7,
                        "status_path_masses": {"realized": 0.7},
                    },
                    {
                        "endpoint_key": "policy_fails",
                        "label": "Policy fails",
                        "status": "eliminated",
                        "realized": False,
                        "path_mass": 0.3,
                        "status_path_masses": {"eliminated": 0.3},
                    },
                ],
            }
        },
    }

    forecast = extract_forecast_predictions(content)["primary"]

    assert forecast["p_yes"] == 0.7
    assert forecast["resolution_state"] == "resolved"
    assert forecast["method"] == "path_mass_binary_endpoint_extraction"
    assert forecast["mass"]["yes"] == 0.7
    assert forecast["mass"]["no"] == 0.3


def test_unresolved_mass_degrades_toward_uncertainty() -> None:
    content = {
        "endpoint_ledger": {
            "payload": {
                "aggregation": "path_mass_by_endpoint_status",
                "path_probability_mass": 1.0,
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "policy_survives",
                        "label": "Policy survives",
                        "status": "realized",
                        "realized": True,
                        "path_mass": 0.6,
                        "status_path_masses": {"realized": 0.6},
                    },
                    {
                        "endpoint_key": "endpoint_insufficient_ticks",
                        "label": "Insufficient ticks",
                        "status": "insufficient_ticks",
                        "realized": None,
                        "path_mass": 0.4,
                        "status_path_masses": {"insufficient_ticks": 0.4},
                    },
                ],
            }
        },
    }

    forecast = extract_forecast_predictions(content)["primary"]

    assert forecast["p_yes"] == 0.8
    assert forecast["resolution_state"] == "inferred"
    assert forecast["mass"]["yes"] == 0.6
    assert forecast["mass"]["uncertain"] == 0.4


def test_process_only_auxiliary_endpoints_do_not_override_binary_evidence() -> None:
    content = {
        "endpoint_ledger": {
            "payload": {
                "aggregation": "path_mass_by_endpoint_status",
                "path_probability_mass": 1.0,
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "policy_survives",
                        "label": "Policy survives",
                        "status": "realized",
                        "realized": True,
                        "path_mass": 0.45,
                        "status_path_masses": {"realized": 0.45},
                    },
                    {
                        "endpoint_key": "policy_fails",
                        "label": "Policy fails",
                        "status": "eliminated",
                        "realized": False,
                        "path_mass": 0.35,
                        "status_path_masses": {"eliminated": 0.35},
                    },
                    {
                        "endpoint_key": "briefing_completed",
                        "label": "Briefing completed",
                        "status": "process_only",
                        "realized": None,
                        "path_mass": 0.2,
                        "status_path_masses": {"process_only": 0.2},
                    },
                ],
            }
        },
    }

    forecast = extract_forecast_predictions(content)["primary"]

    assert forecast["p_yes"] == 0.55
    assert forecast["mass"]["uncertain"] == 0.2
    assert forecast["excluded_auxiliary_endpoint_keys"] == ["briefing_completed"]


def test_explicit_predicate_resolution_overrides_endpoint_fallback() -> None:
    content = {
        "scenario_question": "Will the emergency policy survive court review?",
        "predicate_resolutions": [
            {
                "predicate_id": "policy_survives",
                "description": "Policy survives court review",
                "type": "binary_event",
                "fired_path_mass": 0.25,
                "total_path_mass": 1.0,
                "hit_rate": 0.25,
            }
        ],
        "endpoint_ledger": {
            "payload": {
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "most_common_endpoint",
                        "label": "Most common endpoint",
                        "status": "realized",
                        "realized": True,
                        "path_mass": 0.9,
                    }
                ],
            }
        },
    }

    forecast = extract_forecast_predictions(content)["primary"]

    assert forecast["endpoint_id"] == "policy_survives"
    assert forecast["p_yes"] == 0.25
    assert forecast["method"] == "predicate_resolution_path_mass"


def test_absent_binary_evidence_returns_uncertain_forecast() -> None:
    content = {
        "endpoint_ledger": {
            "payload": {
                "aggregation": "path_mass_by_endpoint_status",
                "path_probability_mass": 1.0,
                "endpoint_path_mass_distribution": [
                    {
                        "endpoint_key": "briefing_completed",
                        "label": "Briefing completed",
                        "status": "process_only",
                        "realized": None,
                        "path_mass": 1.0,
                        "status_path_masses": {"process_only": 1.0},
                    }
                ],
            }
        },
    }

    forecast = extract_forecast_predictions(content)["primary"]

    assert forecast["p_yes"] == 0.5
    assert forecast["resolution_state"] == "uncertain"
    assert forecast["confidence"] == 0.0
