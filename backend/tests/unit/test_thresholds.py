"""Unit tests for backend.app.sociology.thresholds."""
from __future__ import annotations


from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams
from backend.app.sociology.thresholds import (
    complex_contagion,
    mobilization_mode_transition,
    mobilization_score,
    will_mobilize,
)
from backend.app.sociology.trust import TrustGraph


def _cohort(**overrides) -> CohortState:
    base = dict(
        cohort_id="coh-1",
        universe_id="u-1",
        tick=0,
        archetype_id="a-1",
        represented_population=500,
        population_share_of_archetype=0.5,
        expression_level=0.4,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.5,
        fatigue=0.1,
        prompt_temperature=0.7,
        representation_mode="population",
    )
    base.update(overrides)
    return CohortState(**base)


def test_mobilization_score_grows_with_grievance():
    params = SociologyParams()
    low = _cohort(grievance=0.0)
    high = _cohort(grievance=0.9)
    assert mobilization_score(
        cohort=high, trusted_peer_participation=0.0, params=params
    ) > mobilization_score(
        cohort=low, trusted_peer_participation=0.0, params=params
    )


def test_mobilization_score_falls_with_cost_fear():
    params = SociologyParams()
    base = _cohort(grievance=0.5)
    risky = _cohort(
        grievance=0.5,
        behavior_state={"legal_or_status_risk_sensitivity": 0.9},
    )
    assert mobilization_score(
        cohort=risky, trusted_peer_participation=0.0, params=params
    ) < mobilization_score(
        cohort=base, trusted_peer_participation=0.0, params=params
    )


def test_will_mobilize_threshold():
    params = SociologyParams()
    assert will_mobilize(params.mobilization.default_threshold + 0.1, params) is True
    assert will_mobilize(0.0, params) is False


def test_complex_contagion_k_threshold():
    """Adoption requires at least k mobilized trusted neighbors."""
    params = SociologyParams()
    # Force k=2 via the mobilization params we control here.
    params.mobilization.k_threshold_complex_contagion = 2

    g = TrustGraph()
    g.add_or_update_edge(
        "ego", "n1", ingroup_affinity=0.9, recent_alignment=0.9, exposure_count=0
    )
    g.add_or_update_edge(
        "ego", "n2", ingroup_affinity=0.9, recent_alignment=0.9, exposure_count=0
    )
    g.add_or_update_edge(
        "ego", "n3", ingroup_affinity=0.9, recent_alignment=0.9, exposure_count=0
    )

    # Only one neighbor mobilized — shouldn't trigger.
    assert (
        complex_contagion(
            cohort_id="ego",
            trust_graph=g,
            mobilized_set={"n1"},
            params=params,
        )
        is False
    )

    # Two mobilized — meets the k=2 threshold.
    assert (
        complex_contagion(
            cohort_id="ego",
            trust_graph=g,
            mobilized_set={"n1", "n2"},
            params=params,
        )
        is True
    )


def test_complex_contagion_ignores_non_neighbors():
    params = SociologyParams()
    params.mobilization.k_threshold_complex_contagion = 1
    g = TrustGraph()
    g.add_or_update_edge(
        "ego", "n1", ingroup_affinity=0.9, recent_alignment=0.9, exposure_count=0
    )
    # 'stranger' isn't connected to 'ego' — even if mobilized it shouldn't help.
    assert (
        complex_contagion(
            cohort_id="ego",
            trust_graph=g,
            mobilized_set={"stranger"},
            params=params,
        )
        is False
    )


def test_mobilization_mode_transition_bands():
    c = _cohort()
    # Below 0.20 -> dormant
    assert mobilization_mode_transition(cohort=c, score=0.0) == "dormant"
    assert mobilization_mode_transition(cohort=c, score=0.10) == "dormant"
    # 0.20+ -> murmur
    assert mobilization_mode_transition(cohort=c, score=0.20) == "murmur"
    # 0.45+ -> organize
    assert mobilization_mode_transition(cohort=c, score=0.50) == "organize"
    # 0.65+ -> mobilize
    assert mobilization_mode_transition(cohort=c, score=0.70) == "mobilize"
    # 0.85+ -> escalate
    assert mobilization_mode_transition(cohort=c, score=0.95) == "escalate"
