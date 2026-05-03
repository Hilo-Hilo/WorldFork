"""Active-agent selection for a tick (PRD §11.2).

Computes per-actor activity scores, selects which cohorts/heroes get LLM
calls this tick, and provides salience helpers used to feed those scores.
The function signatures here are stable contracts for B4-C tick_runner.
"""
from __future__ import annotations

import math

from backend.app.schemas.actors import CohortState, HeroState
from backend.app.schemas.events import Event
from backend.app.schemas.posts import SocialPost

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_ACTIVITY = 0.3
_MAX_SCORE = 5.0


def _clip(value: float, lo: float = 0.0, hi: float = _MAX_SCORE) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Per-actor scoring
# ---------------------------------------------------------------------------


def activity_score(
    cohort: CohortState,
    *,
    event_salience: float,
    queued_event_pressure: float,
    social_pressure: float,
) -> float:
    """§11.2 cohort activity score, clipped to [0, 5]."""
    raw = (
        _BASE_ACTIVITY
        + float(event_salience)
        + float(cohort.attention)
        + float(cohort.expression_level)
        + float(queued_event_pressure)
        + float(social_pressure)
        - float(cohort.fatigue)
    )
    return _clip(raw)


def hero_activity_score(
    hero: HeroState,
    *,
    event_salience: float,
    queued_event_pressure: float,
    social_pressure: float,
) -> float:
    """Hero analog of §11.2 score.  Heroes don't have an `expression_level`
    field; we substitute ``perceived_pressure`` which captures comparable
    drive to act."""
    raw = (
        _BASE_ACTIVITY
        + float(event_salience)
        + float(hero.attention)
        + float(hero.perceived_pressure)
        + float(queued_event_pressure)
        + float(social_pressure)
        - float(hero.fatigue)
    )
    return _clip(raw)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

_MOBILIZATION_OVERRIDES = {"organize", "mobilize", "escalate"}


def select_active_cohorts(
    cohorts: list[CohortState],
    *,
    salience_lookup: dict[str, float],
    top_k: int | None = None,
    threshold: float = 0.5,
) -> list[CohortState]:
    """Return cohorts that should receive an LLM call this tick.

    A cohort is included if **any** of these hold:

    1. ``cohort.is_active`` is True (operator-pinned active).
    2. ``cohort.mobilization_mode`` is one of ``organize|mobilize|escalate``.
    3. Its computed ``activity_score`` is above ``threshold``.

    If ``top_k`` is provided, the result is capped to the highest-scoring
    ``top_k`` after mandatory inclusions (overrides always win).
    """
    scored: list[tuple[float, CohortState]] = []
    overrides: list[CohortState] = []

    for c in cohorts:
        sal = float(salience_lookup.get(c.cohort_id, 0.0))
        score = activity_score(
            c,
            event_salience=sal,
            queued_event_pressure=min(1.0, len(c.queued_event_ids) * 0.25),
            social_pressure=min(1.0, len(c.recent_post_ids) * 0.05),
        )
        scored.append((score, c))

        if c.is_active or c.mobilization_mode in _MOBILIZATION_OVERRIDES:
            overrides.append(c)

    # Sort by score descending; deterministic tie-break by cohort_id.
    scored.sort(key=lambda pair: (-pair[0], pair[1].cohort_id))

    above_threshold = [c for s, c in scored if s >= threshold]

    # Merge overrides + above_threshold preserving order, deduplicating by id.
    merged_ids: set[str] = set()
    selected: list[CohortState] = []
    for c in list(overrides) + above_threshold:
        if c.cohort_id in merged_ids:
            continue
        merged_ids.add(c.cohort_id)
        selected.append(c)

    if top_k is not None:
        # Keep all overrides first, then top_k - len(overrides) of the rest.
        override_ids = {c.cohort_id for c in overrides}
        head = [c for c in selected if c.cohort_id in override_ids]
        tail = [c for c in selected if c.cohort_id not in override_ids]
        slack = max(0, top_k - len(head))
        selected = head + tail[:slack]

    return selected


def select_active_heroes(
    heroes: list[HeroState],
    *,
    salience_lookup: dict[str, float],
    top_k: int | None = None,
    threshold: float = 0.4,
) -> list[HeroState]:
    """Hero analog of :func:`select_active_cohorts`."""
    scored: list[tuple[float, HeroState]] = []
    for h in heroes:
        sal = float(salience_lookup.get(h.hero_id, 0.0))
        score = hero_activity_score(
            h,
            event_salience=sal,
            queued_event_pressure=min(1.0, len(h.queued_events) * 0.25),
            social_pressure=min(1.0, len(h.recent_posts) * 0.05),
        )
        scored.append((score, h))

    scored.sort(key=lambda pair: (-pair[0], pair[1].hero_id))
    selected = [h for s, h in scored if s >= threshold]

    if top_k is not None:
        selected = selected[:top_k]

    return selected


# ---------------------------------------------------------------------------
# Salience estimators
# ---------------------------------------------------------------------------


def estimate_event_salience(event: Event, *, current_tick: int) -> float:
    """Proximity-decayed salience contribution for one event.

    Formula::

        salience = exp(-|scheduled_tick - current_tick| / 3)
                   * risk_level
                   * visibility_weight

    Returns a float in roughly [0, 1] (capped at 1.0).
    """
    delta = abs(int(event.scheduled_tick) - int(current_tick))
    decay = math.exp(-delta / 3.0)
    visibility_weights = {
        "public": 1.0,
        "institution": 0.7,
        "cohort": 0.5,
        "invite": 0.4,
        "private": 0.2,
    }
    vw = visibility_weights.get(event.visibility, 0.6)
    raw = decay * float(event.risk_level) * vw
    return min(1.0, max(0.0, raw))


def estimate_social_pressure(
    cohort: CohortState,
    recent_posts_in_feed: list[SocialPost],
) -> float:
    """Social pressure on a cohort from its visible feed.

    Combines post volume, average reach, and average emotional intensity
    (mean emotion magnitude across known emotions) into a single score in
    [0, 1].  Empty feed -> 0.
    """
    if not recent_posts_in_feed:
        return 0.0

    n = len(recent_posts_in_feed)
    volume_term = min(1.0, n / 20.0)  # saturates at 20 posts.

    reach_sum = sum(float(p.reach_score) for p in recent_posts_in_feed)
    avg_reach = reach_sum / n  # already in [0,1] range.

    intensity_sum = 0.0
    intensity_count = 0
    for p in recent_posts_in_feed:
        for _, val in (p.emotion_signal or {}).items():
            intensity_sum += abs(float(val))
            intensity_count += 1
    avg_intensity = (intensity_sum / max(1, intensity_count)) / 10.0

    # Weighted mix; tuned to keep result in [0, 1].
    raw = 0.4 * volume_term + 0.4 * avg_reach + 0.2 * avg_intensity
    # Cohorts with high attention amplify perceived pressure.
    raw *= 0.6 + 0.4 * float(cohort.attention)
    return min(1.0, max(0.0, raw))


__all__ = [
    "activity_score",
    "hero_activity_score",
    "select_active_cohorts",
    "select_active_heroes",
    "estimate_event_salience",
    "estimate_social_pressure",
]
