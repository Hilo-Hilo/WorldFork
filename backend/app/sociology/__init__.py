"""
backend.app.sociology — sociology engine.

Implements the sociology model transitions and the transactional split/merge layer that
ENFORCES population conservation. Public re-exports below are the stable
API; internal helpers (numpy code, kernel math) live in the submodules.
"""
from __future__ import annotations

from backend.app.sociology.attention import (
    update_attention,
    update_attention_batch,
)
from backend.app.sociology.belief import (
    trust_weighted_persuasion,
    update_beliefs,
)
from backend.app.sociology.expression import (
    spiral_of_silence_gate,
    update_expression,
)
from backend.app.sociology.graphs import MultiplexGraph
from backend.app.sociology.homophily import rewire
from backend.app.sociology.identity import update_identity_salience
from backend.app.sociology.parameters import (
    DEFAULT_PARAMS,
    load_sociology_params,
)
from backend.app.sociology.split_merge import (
    audit_population_conservation,
    commit_merge,
    commit_split,
    evaluate_merge_validity,
    evaluate_split_validity,
)
from backend.app.sociology.thresholds import (
    complex_contagion,
    mobilization_mode_transition,
    mobilization_score,
    will_mobilize,
)
from backend.app.sociology.transitions import run_all_transitions
from backend.app.sociology.trust import TrustGraph

# Aliases preserve the public API names used by LLM proposal handling.
propose_splits = evaluate_split_validity
propose_merges = evaluate_merge_validity


__all__ = [
    # attention
    "update_attention",
    "update_attention_batch",
    # expression
    "update_expression",
    "spiral_of_silence_gate",
    # belief
    "update_beliefs",
    "trust_weighted_persuasion",
    # trust
    "TrustGraph",
    # thresholds / mobilization
    "mobilization_score",
    "will_mobilize",
    "complex_contagion",
    "mobilization_mode_transition",
    # graphs
    "MultiplexGraph",
    # split / merge
    "evaluate_split_validity",
    "commit_split",
    "evaluate_merge_validity",
    "commit_merge",
    "audit_population_conservation",
    "propose_splits",
    "propose_merges",
    # homophily
    "rewire",
    # identity
    "update_identity_salience",
    # parameters
    "load_sociology_params",
    "DEFAULT_PARAMS",
    # orchestration
    "run_all_transitions",
]
