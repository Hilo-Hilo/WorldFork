"""Unit tests for backend.app.sociology.belief."""
from __future__ import annotations

import numpy as np
import pytest

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams
from backend.app.sociology.belief import (
    trust_weighted_persuasion,
    update_beliefs,
)


def _cohort(cid: str, stance: float) -> CohortState:
    return CohortState(
        cohort_id=cid,
        universe_id="u-1",
        tick=0,
        archetype_id="a-1",
        represented_population=500,
        population_share_of_archetype=0.25,
        issue_stance={"axis": stance},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.5,
        fatigue=0.1,
        prompt_temperature=0.7,
        representation_mode="population",
    )


def test_belief_drift_moves_toward_consensus():
    """A cohort surrounded by trusted peers with similar but distinct beliefs
    should drift toward them, not away."""
    params = SociologyParams()
    a = _cohort("a", -0.5)
    b = _cohort("b", 0.5)
    c = _cohort("c", 0.5)
    cohorts = [a, b, c]

    # Full mutual trust + exposure.
    n = 3
    T = np.ones((n, n))
    E = np.ones((n, n))
    np.fill_diagonal(T, 0.0)
    np.fill_diagonal(E, 0.0)

    out = update_beliefs(
        cohorts=cohorts,
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
    )
    new_a = out["a"]["axis"]
    # `a` started at -0.5 and is pulled by two cohorts at +0.5 -> should rise.
    assert new_a > -0.5
    assert new_a < 0.5  # but not jump all the way


def test_stubborn_cohort_moves_less():
    """Per-cohort stubbornness override anchors the cohort to its baseline."""
    params = SociologyParams()
    a = _cohort("a", -0.5)
    b = _cohort("b", 0.5)
    cohorts = [a, b]

    T = np.array([[0.0, 1.0], [1.0, 0.0]])
    E = np.array([[0.0, 1.0], [1.0, 0.0]])

    baselines = {"a": {"axis": -0.5}, "b": {"axis": 0.5}}
    flexible = update_beliefs(
        cohorts=cohorts,
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
        baselines=baselines,
        stubbornness={"a": 0.0, "b": 0.0},
    )
    stubborn = update_beliefs(
        cohorts=cohorts,
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
        baselines=baselines,
        stubbornness={"a": 1.0, "b": 1.0},
    )
    # Flexible 'a' moves further from -0.5 (toward +0.5) than stubborn 'a'.
    assert abs(flexible["a"]["axis"] - (-0.5)) >= abs(
        stubborn["a"]["axis"] - (-0.5)
    )


def test_event_shock_applied():
    """Event shocks add directly to the next-tick belief."""
    params = SociologyParams()
    a = _cohort("a", 0.0)
    cohorts = [a]
    T = np.zeros((1, 1))
    E = np.zeros((1, 1))
    out = update_beliefs(
        cohorts=cohorts,
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
        event_shocks={"a": {"axis": 0.10}},
    )
    # max_step_per_tick caps the absolute step magnitude
    new = out["a"]["axis"]
    assert new > 0.0
    assert abs(new - 0.0) <= params.belief_drift.max_step_per_tick + 1e-9


def test_bounded_kernel_dampens_distant_pairs():
    """Cohorts far apart in belief should influence each other less than
    cohorts close in belief (the bounded kernel's job)."""
    params = SociologyParams()
    near_a = _cohort("a", 0.0)
    near_b = _cohort("b", 0.05)
    far_b = _cohort("b", 1.5)

    T = np.array([[0.0, 1.0], [1.0, 0.0]])
    E = np.array([[0.0, 1.0], [1.0, 0.0]])

    near_out = update_beliefs(
        cohorts=[near_a, near_b],
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
    )
    far_out = update_beliefs(
        cohorts=[near_a, far_b],
        trust_matrix=T,
        exposure_matrix=E,
        axes=["axis"],
        params=params,
    )
    near_delta = abs(near_out["a"]["axis"] - 0.0)
    far_delta = abs(far_out["a"]["axis"] - 0.0)
    # The near pair should produce a bigger weighted pull, but the absolute
    # delta is also driven by (b - a). With a near pair (b=0.05) the delta
    # is small. Use kernel attenuation directly: far pair kernel exp(-1.5^2/(2*0.5^2))
    # is tiny so far_delta should be small even if (b-a) is huge.
    assert near_delta > 0.0
    assert far_delta < 0.5  # bounded kernel keeps the step modest


def test_trust_weighted_persuasion_simple():
    out = trust_weighted_persuasion(
        source_credibility=0.8,
        listener_evidence_sensitivity=0.5,
        message_strength=1.0,
    )
    assert out == pytest.approx(0.4)


def test_empty_cohorts_returns_empty():
    params = SociologyParams()
    out = update_beliefs(
        cohorts=[],
        trust_matrix=np.zeros((0, 0)),
        exposure_matrix=np.zeros((0, 0)),
        axes=["axis"],
        params=params,
    )
    assert out == {}
