"""Unit tests for backend.app.runtime.active_selection."""
from __future__ import annotations


from backend.app.schemas.actors import CohortState, HeroState
from backend.app.schemas.events import Event
from backend.app.runtime.active_selection import (
    activity_score,
    estimate_event_salience,
    estimate_social_pressure,
    select_active_cohorts,
    select_active_heroes,
)


def _cohort(
    *,
    cohort_id: str = "c1",
    pop: int = 1000,
    expression: float = 0.4,
    attention: float = 0.5,
    fatigue: float = 0.2,
    mobilization: str = "murmur",
    is_active: bool = False,
) -> CohortState:
    return CohortState(
        cohort_id=cohort_id,
        universe_id="U000",
        tick=0,
        archetype_id="arch1",
        represented_population=pop,
        population_share_of_archetype=0.1,
        issue_stance={"primary_issue": 0.0},
        expression_level=expression,
        mobilization_mode=mobilization,
        speech_mode="public",
        emotions={"anger": 5.0},
        behavior_state={},
        attention=attention,
        fatigue=fatigue,
        prompt_temperature=0.5,
        representation_mode="population",
        is_active=is_active,
    )


# ---------------------------------------------------------------------------


class TestActivityScore:
    def test_score_increases_with_attention_and_expression(self):
        low = _cohort(attention=0.1, expression=0.1, fatigue=0.5)
        high = _cohort(attention=0.9, expression=0.9, fatigue=0.0)
        s_low = activity_score(low, event_salience=0.0,
                               queued_event_pressure=0.0, social_pressure=0.0)
        s_high = activity_score(high, event_salience=0.0,
                                queued_event_pressure=0.0, social_pressure=0.0)
        assert s_high > s_low

    def test_score_clipped_within_range(self):
        c = _cohort(attention=1.0, expression=1.0, fatigue=0.0)
        s = activity_score(c, event_salience=5.0,
                           queued_event_pressure=5.0, social_pressure=5.0)
        assert 0.0 <= s <= 5.0


# ---------------------------------------------------------------------------


class TestSelectActiveCohorts:
    def test_returns_above_threshold(self):
        active = _cohort(cohort_id="active", attention=0.9, expression=0.9, fatigue=0.0)
        sleepy = _cohort(cohort_id="sleepy", attention=0.0, expression=0.0, fatigue=0.9)
        sel = select_active_cohorts(
            [active, sleepy],
            salience_lookup={},
            threshold=0.8,
        )
        ids = [c.cohort_id for c in sel]
        assert "active" in ids
        assert "sleepy" not in ids

    def test_mobilization_override_always_selects(self):
        # Sleepy cohort but mobilization_mode="organize" forces inclusion.
        sleepy_organizer = _cohort(
            cohort_id="organizer",
            attention=0.0, expression=0.0, fatigue=0.9,
            mobilization="organize",
        )
        sel = select_active_cohorts(
            [sleepy_organizer],
            salience_lookup={},
            threshold=10.0,  # impossible to clear via score
        )
        assert any(c.cohort_id == "organizer" for c in sel)

    def test_is_active_flag_overrides(self):
        sleepy_pinned = _cohort(
            cohort_id="pinned", attention=0.0, expression=0.0, fatigue=0.9,
            is_active=True,
        )
        sel = select_active_cohorts(
            [sleepy_pinned],
            salience_lookup={},
            threshold=10.0,
        )
        assert any(c.cohort_id == "pinned" for c in sel)

    def test_top_k_caps_results(self):
        cohorts = [
            _cohort(cohort_id=f"c{i}", attention=0.9, expression=0.9, fatigue=0.0)
            for i in range(10)
        ]
        sel = select_active_cohorts(
            cohorts,
            salience_lookup={},
            top_k=3,
            threshold=0.0,
        )
        assert len(sel) == 3


# ---------------------------------------------------------------------------


class TestSelectActiveHeroes:
    def test_basic_selection(self):
        active = HeroState(
            hero_id="h_active", universe_id="U", tick=0,
            current_emotions={}, current_issue_stances={},
            attention=0.9, fatigue=0.1, perceived_pressure=0.8,
        )
        sleepy = HeroState(
            hero_id="h_sleepy", universe_id="U", tick=0,
            current_emotions={}, current_issue_stances={},
            attention=0.05, fatigue=0.9, perceived_pressure=0.05,
        )
        sel = select_active_heroes(
            [active, sleepy], salience_lookup={}, threshold=0.5
        )
        ids = [h.hero_id for h in sel]
        assert "h_active" in ids
        assert "h_sleepy" not in ids


# ---------------------------------------------------------------------------


class TestEventSalience:
    def test_salience_decays_with_distance(self):
        e = Event(
            event_id="e1", universe_id="U", created_tick=0, scheduled_tick=1,
            event_type="protest", title="t", description="d",
            created_by_actor_id="x", visibility="public", risk_level=0.5,
            status="scheduled",
        )
        e_far = Event(
            event_id="e2", universe_id="U", created_tick=0, scheduled_tick=20,
            event_type="protest", title="t", description="d",
            created_by_actor_id="x", visibility="public", risk_level=0.5,
            status="scheduled",
        )
        s_close = estimate_event_salience(e, current_tick=0)
        s_far = estimate_event_salience(e_far, current_tick=0)
        assert s_close > s_far
        assert s_far < 0.01

    def test_visibility_affects_salience(self):
        e_pub = Event(
            event_id="e1", universe_id="U", created_tick=0, scheduled_tick=0,
            event_type="protest", title="t", description="d",
            created_by_actor_id="x", visibility="public", risk_level=0.5,
            status="scheduled",
        )
        e_priv = Event(
            event_id="e2", universe_id="U", created_tick=0, scheduled_tick=0,
            event_type="protest", title="t", description="d",
            created_by_actor_id="x", visibility="private", risk_level=0.5,
            status="scheduled",
        )
        assert estimate_event_salience(e_pub, current_tick=0) > estimate_event_salience(
            e_priv, current_tick=0
        )


class TestSocialPressure:
    def test_empty_feed_zero(self):
        c = _cohort()
        assert estimate_social_pressure(c, []) == 0.0

    def test_pressure_increases_with_volume(self):
        from backend.app.schemas.posts import SocialPost

        c = _cohort()
        few = [
            SocialPost(
                post_id=f"p{i}", universe_id="U", platform="x", tick_created=0,
                author_actor_id="a", content="hi",
                credibility_signal=0.5, visibility_scope="public",
                reach_score=0.5, repost_count=0, comment_count=0,
            )
            for i in range(2)
        ]
        many = [
            SocialPost(
                post_id=f"p{i}", universe_id="U", platform="x", tick_created=0,
                author_actor_id="a", content="hi",
                credibility_signal=0.5, visibility_scope="public",
                reach_score=0.5, repost_count=0, comment_count=0,
            )
            for i in range(20)
        ]
        assert estimate_social_pressure(c, few) < estimate_social_pressure(c, many)
