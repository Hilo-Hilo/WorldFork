"""Unit tests for backend.app.sociology.expression."""
from __future__ import annotations


from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams
from backend.app.sociology.expression import (
    spiral_of_silence_gate,
    update_expression,
)


def _make_cohort(**overrides) -> CohortState:
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


def test_anger_raises_expression():
    params = SociologyParams()
    low_anger = _make_cohort(emotions={"anger": 0.0})
    high_anger = _make_cohort(emotions={"anger": 8.0})
    e_low = update_expression(cohort=low_anger, params=params)
    e_high = update_expression(cohort=high_anger, params=params)
    assert e_high > e_low


def test_fear_of_isolation_lowers_expression():
    params = SociologyParams()
    no_fear = _make_cohort(fear_of_isolation=0.0, expression_level=0.5)
    high_fear = _make_cohort(fear_of_isolation=0.9, expression_level=0.5)
    e_no = update_expression(cohort=no_fear, params=params)
    e_yes = update_expression(cohort=high_fear, params=params)
    assert e_yes < e_no


def test_fatigue_lowers_expression():
    params = SociologyParams()
    rested = _make_cohort(fatigue=0.0, expression_level=0.5)
    tired = _make_cohort(fatigue=0.9, expression_level=0.5)
    assert update_expression(cohort=tired, params=params) < update_expression(
        cohort=rested, params=params
    )


def test_expression_clamps_to_unit_range():
    params = SociologyParams()
    extreme_high = _make_cohort(
        expression_level=1.0,
        emotions={"anger": 10.0, "urgency": 10.0},
        perceived_efficacy=1.0,
    )
    e = update_expression(cohort=extreme_high, params=params)
    assert 0.0 <= e <= 1.0

    extreme_low = _make_cohort(
        expression_level=0.0,
        fear_of_isolation=1.0,
        fatigue=1.0,
    )
    e2 = update_expression(cohort=extreme_low, params=params)
    assert 0.0 <= e2 <= 1.0


def test_spiral_of_silence_gate_kicks_in_under_pressure():
    params = SociologyParams()
    fearful = _make_cohort(fear_of_isolation=0.95)
    silent = spiral_of_silence_gate(
        cohort=fearful,
        perceived_minority_status=0.9,
        institutional_risk=0.5,
        params=params,
    )
    assert silent is True


def test_spiral_of_silence_gate_quiet_when_courage_high():
    params = SociologyParams()
    courageous = _make_cohort(
        fear_of_isolation=0.05,
        behavior_state={"contrarianism": 0.9},
    )
    silent = spiral_of_silence_gate(
        cohort=courageous,
        perceived_minority_status=0.1,
        institutional_risk=0.0,
        params=params,
    )
    assert silent is False
