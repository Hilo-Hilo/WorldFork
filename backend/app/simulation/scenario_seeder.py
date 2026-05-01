"""Deterministic seeders for initial cohort state from an archetype.

These helpers take a :class:`PopulationArchetype` (already validated against
PRD §9.3) and produce the *initial* emotion / issue-stance / expression-band
slice used by :func:`backend.app.domains.big_bang.initializer.initialize_big_bang`
when it instantiates the seed cohort for each archetype at tick 0.

All functions are pure / deterministic given the inputs — no I/O, no LLM —
so the initializer's output is reproducible up to the LLM-draft step.
"""
from __future__ import annotations

from backend.app.schemas.actors import PopulationArchetype
from backend.app.schemas.common import clamp01, clamp_emotion

# ---------------------------------------------------------------------------
# Default emotion seed
# ---------------------------------------------------------------------------

# 12 emotions per source_of_truth/emotions.json
_EMOTION_KEYS: tuple[str, ...] = (
    "anger",
    "fear",
    "anxiety",
    "hope",
    "trust",
    "distrust",
    "joy",
    "sadness",
    "disgust",
    "surprise",
    "pride",
    "shame",
)


def _institutional_trust_axis(archetype: PopulationArchetype) -> float:
    """Return the value of an axis named ``institutional_trust`` if present.

    Falls back to 0.0 (neutral). Values are clamped to [-1, 1]; PRD ideology
    axes are bipolar.
    """
    raw = archetype.ideology_axes.get("institutional_trust", 0.0)
    return max(-1.0, min(1.0, float(raw)))


def _cultural_axis(archetype: PopulationArchetype) -> float:
    """Return the cultural / progressive-vs-traditional axis if present.

    Tries ``cultural``, then ``cultural_axis``, then ``progressive``;
    returns 0.0 otherwise.  Bipolar [-1, 1].
    """
    for key in ("cultural", "cultural_axis", "progressive_vs_traditional"):
        if key in archetype.ideology_axes:
            return max(-1.0, min(1.0, float(archetype.ideology_axes[key])))
    return 0.0


def derive_default_emotions(
    archetype: PopulationArchetype, scenario_text: str
) -> dict[str, float]:
    """Deterministic baseline emotion vector for a fresh cohort at tick 0.

    Heuristics (all values in PRD's emotion scale [0, 10]):

    - ``trust`` increases with institutional_trust_axis and ingroup_affinities.
    - ``distrust`` is the symmetric reflection of ``trust``.
    - ``fear`` and ``anxiety`` are seeded by the inverse of cultural axis +
      the actor's ``vulnerability_to_policy``.
    - ``hope`` is seeded by ``ability_to_influence_outcome``.
    - ``anger`` is seeded by ``material_stake`` minus ``perceived_efficacy``
      proxy (we don't have that yet, so use ``ability_to_influence_outcome``).

    The returned dict always contains all 12 SoT emotion keys with non-NaN
    values clamped to [0, 10]. ``scenario_text`` is currently unused but is
    accepted so future work can incorporate scenario-keyword sentiment.
    """
    _ = scenario_text  # reserved for future keyword scoring

    inst_trust = _institutional_trust_axis(archetype)        # in [-1, 1]
    cultural = _cultural_axis(archetype)                     # in [-1, 1]
    vuln = clamp01(archetype.vulnerability_to_policy)        # [0, 1]
    influence = clamp01(archetype.ability_to_influence_outcome)
    material = clamp01(archetype.material_stake)
    symbolic = clamp01(archetype.symbolic_stake)

    trust_score = max(0.0, 4.0 + inst_trust * 3.5)           # baseline 4
    distrust_score = max(0.0, 4.0 - inst_trust * 3.5)
    fear_score = clamp_emotion(2.5 + vuln * 4.0 + max(0.0, -cultural) * 1.5)
    anxiety_score = clamp_emotion(2.0 + vuln * 3.5 + symbolic * 1.0)
    hope_score = clamp_emotion(2.0 + influence * 4.5)
    anger_score = clamp_emotion(1.5 + material * 4.0 - influence * 1.0)
    sadness_score = clamp_emotion(1.0 + vuln * 2.0)
    disgust_score = clamp_emotion(1.0 + max(0.0, -inst_trust) * 2.0)
    pride_score = clamp_emotion(1.5 + max(0.0, cultural) * 2.0 + symbolic * 1.0)
    shame_score = clamp_emotion(0.5)
    joy_score = clamp_emotion(2.0 + influence * 1.0)
    surprise_score = clamp_emotion(1.0)

    out: dict[str, float] = {
        "anger": anger_score,
        "fear": fear_score,
        "anxiety": anxiety_score,
        "hope": hope_score,
        "trust": clamp_emotion(trust_score),
        "distrust": clamp_emotion(distrust_score),
        "joy": joy_score,
        "sadness": sadness_score,
        "disgust": disgust_score,
        "surprise": surprise_score,
        "pride": pride_score,
        "shame": shame_score,
    }

    # Backfill any missing SoT keys with 0.0 (defensive — should never trigger
    # given the explicit dict above).
    for key in _EMOTION_KEYS:
        out.setdefault(key, 0.0)

    return out


# ---------------------------------------------------------------------------
# Default issue-stance seed
# ---------------------------------------------------------------------------


def derive_default_issue_stance(
    archetype: PopulationArchetype, scenario_text: str
) -> dict[str, float]:
    """Seed the cohort's stance on the canonical primary_issue axis.

    Strategy: combine the archetype's economic + cultural + institutional
    axes (if present) into a single signal in [-1, 1] expressing whether
    the cohort is opposed (-) or supportive (+) of the scenario proposal.
    The default ``primary_issue`` key matches
    ``source_of_truth/issue_stance_axes.json``.

    ``scenario_text`` is reserved for future keyword polarity inference.
    """
    _ = scenario_text

    eco = float(archetype.ideology_axes.get("economic", 0.0))
    cult = _cultural_axis(archetype)
    inst = _institutional_trust_axis(archetype)
    material = clamp01(archetype.material_stake)

    # Weighted average; archetypes with high material_stake feel the issue
    # more strongly in either direction — so we amplify by (1 + material).
    raw = (eco * 0.35 + cult * 0.25 + inst * 0.20) * (1.0 + 0.5 * material)
    stance = max(-1.0, min(1.0, raw))

    return {"primary_issue": round(stance, 4)}


# ---------------------------------------------------------------------------
# Initial expression band
# ---------------------------------------------------------------------------

# Bands per source_of_truth/expression_scale.json (7 bands). We pick a
# representative midpoint per band so the cohort starts inside the named band
# and the expression-update equation can move it freely.
_BAND_MIDPOINTS: tuple[float, ...] = (
    0.05,   # negligent_unaware
    0.18,   # silent_observer
    0.32,   # low_level_discussant
    0.50,   # active_speaker
    0.68,   # advocate
    0.82,   # organizer
    0.95,   # high_risk_escalator
)


def derive_initial_expression(archetype: PopulationArchetype) -> float:
    """Pick an initial expression-band midpoint for the seed cohort.

    Combines ``material_stake``, ``symbolic_stake``, and
    ``ability_to_influence_outcome`` into a [0, 1] expression score.  Then
    snap to the closest band midpoint per PRD §8.4.

    The 'high_risk_escalator' band (0.90–1.00) is intentionally never picked
    at tick 0 — escalation should be earned through the simulation, not
    assumed at Big Bang.
    """
    material = clamp01(archetype.material_stake)
    symbolic = clamp01(archetype.symbolic_stake)
    influence = clamp01(archetype.ability_to_influence_outcome)
    coordination = clamp01(archetype.coordination_capacity)

    # Score ranges over [0, ~1] with material_stake the dominant signal.
    score = (
        material * 0.40
        + symbolic * 0.20
        + influence * 0.25
        + coordination * 0.15
    )
    score = clamp01(score)

    # Snap to the closest non-escalator band midpoint.
    safe_mids = _BAND_MIDPOINTS[:-1]   # exclude high_risk_escalator
    closest = min(safe_mids, key=lambda mid: abs(mid - score))
    return round(closest, 4)


# ---------------------------------------------------------------------------
# Helper for expression-band → speech/mobilization mode
# ---------------------------------------------------------------------------


def derive_initial_modes(expression: float) -> tuple[str, str]:
    """Map an expression score in [0, 1] to ``(mobilization_mode, speech_mode)``.

    The two enums are PRD §9.4. Cohorts at tick 0 start with a band-aligned
    speech mode and dormant/murmur mobilization unless their expression is
    very high.
    """
    if expression < 0.10:
        return "dormant", "silent"
    if expression < 0.25:
        return "dormant", "private"
    if expression < 0.40:
        return "murmur", "private"
    if expression < 0.60:
        return "murmur", "semi_public"
    if expression < 0.75:
        return "organize", "public"
    if expression < 0.90:
        return "organize", "loud"
    return "mobilize", "loud"


def derive_representation_mode(population: int) -> str:
    """Population → §11.3 representation_mode literal."""
    if population <= 25:
        return "micro"
    if population <= 250:
        return "small"
    if population <= 5_000:
        return "population"
    return "mass"


def derive_prompt_temperature(population: int) -> float:
    """Population → §11.3 mid-band temperature.  Matches PromptBuilder bands."""
    if population <= 1:
        return 0.85
    if population <= 25:
        return 0.9
    if population <= 250:
        return 0.7
    if population <= 5_000:
        return 0.475
    return 0.325
