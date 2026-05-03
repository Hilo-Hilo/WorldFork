"""Unit tests for backend.app.runtime.metrics."""
from __future__ import annotations

from backend.app.schemas.actors import CohortState
from backend.app.runtime.metrics import (
    compute_divergence,
    compute_universe_metrics,
    emotional_volatility,
)


def _cohort(
    *,
    cohort_id: str = "c1",
    pop: int = 1000,
    emotions: dict | None = None,
    issue_stance: dict | None = None,
    expression: float = 0.4,
    mobilization: str = "murmur",
    is_active: bool = True,
    grievance: float = 0.3,
) -> CohortState:
    return CohortState(
        cohort_id=cohort_id,
        universe_id="U000",
        tick=0,
        archetype_id="arch1",
        represented_population=pop,
        population_share_of_archetype=0.1,
        issue_stance=issue_stance if issue_stance is not None else {"primary_issue": 0.0},
        expression_level=expression,
        mobilization_mode=mobilization,
        speech_mode="public",
        emotions=emotions if emotions is not None else {"anger": 5.0, "trust": 5.0},
        behavior_state={},
        attention=0.5,
        fatigue=0.2,
        grievance=grievance,
        prompt_temperature=0.5,
        representation_mode="population",
        is_active=is_active,
    )


# ---------------------------------------------------------------------------


class TestComputeUniverseMetrics:
    def test_dominant_emotion_picks_correct(self):
        c1 = _cohort(cohort_id="c1", pop=1000, emotions={"anger": 8.0, "fear": 2.0})
        c2 = _cohort(cohort_id="c2", pop=500, emotions={"anger": 6.0, "fear": 4.0})
        m = compute_universe_metrics([c1, c2], [], [], prev_metrics=None)
        assert m["dominant_emotion"][0] == "anger"
        assert m["dominant_emotion"][1] > m["emotion_means"]["fear"]

    def test_active_cohorts_count_only_active(self):
        active = _cohort(cohort_id="a", is_active=True)
        inactive = _cohort(cohort_id="b", is_active=False)
        m = compute_universe_metrics([active, inactive], [], [], prev_metrics=None)
        assert m["active_cohorts"] == 1

    def test_total_population_modeled(self):
        c1 = _cohort(cohort_id="a", pop=1500)
        c2 = _cohort(cohort_id="b", pop=500)
        m = compute_universe_metrics([c1, c2], [], [], prev_metrics=None)
        assert m["total_population_modeled"] == 2000

    def test_mobilization_risk_zero_when_no_organizers(self):
        c1 = _cohort(cohort_id="a", mobilization="murmur")
        m = compute_universe_metrics([c1], [], [], prev_metrics=None)
        assert m["mobilization_risk"] == 0.0

    def test_mobilization_risk_positive_with_organizers(self):
        c1 = _cohort(
            cohort_id="a",
            mobilization="organize",
            emotions={"anger": 9.0},
            grievance=0.8,
        )
        m = compute_universe_metrics([c1], [], [], prev_metrics=None)
        assert m["mobilization_risk"] > 0.0

    def test_emits_required_keys(self):
        m = compute_universe_metrics([_cohort()], [], [], prev_metrics=None)
        for key in ("tick", "active_cohorts", "total_population_modeled",
                    "dominant_emotion", "emotion_means", "expression_mass",
                    "mobilization_risk", "pending_events", "branch_count",
                    "post_volume", "post_reach_total", "issue_polarization",
                    "trust_index", "divergence_vs_parent"):
            assert key in m


class TestComputeDivergence:
    def test_identical_metrics_zero_divergence(self):
        c = _cohort()
        m = compute_universe_metrics([c], [], [], prev_metrics=None)
        assert compute_divergence(m, m) == 0.0

    def test_different_metrics_nonnegative(self):
        c1 = _cohort(cohort_id="a", expression=0.1)
        c2 = _cohort(cohort_id="b", expression=0.9)
        m_a = compute_universe_metrics([c1], [], [], prev_metrics=None)
        m_b = compute_universe_metrics([c2], [], [], prev_metrics=None)
        assert compute_divergence(m_a, m_b) >= 0.0

    def test_divergence_against_zero_vector_is_max(self):
        c = _cohort()
        m = compute_universe_metrics([c], [], [], prev_metrics=None)
        # Zero vector vs nonzero vector → cosine similarity 0 → distance 1.
        assert compute_divergence(m, {}) == 1.0


class TestEmotionalVolatility:
    def test_zero_for_short_history(self):
        c = _cohort()
        assert emotional_volatility([c]) == 0.0
        assert emotional_volatility([]) == 0.0

    def test_positive_for_varying_history(self):
        c1 = _cohort(emotions={"anger": 1.0})
        c2 = _cohort(emotions={"anger": 9.0})
        assert emotional_volatility([c1, c2]) > 0.0
