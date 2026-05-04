"""WorldFork recursive multiverse branching engine (branching runtime).

Public API (B4-A — God-agent reviewer + branch-policy gate):

* :func:`god_review` — async entrypoint that runs one God-agent review for a
  universe at a tick.  Returns the parsed :class:`GodReviewOutput`.
* :func:`evaluate_branch_policy` — pure function that applies the §13.5
  explosion controls to a proposed God decision.
* :class:`BranchPolicy` — re-export from :mod:`backend.app.schemas.branching`.
* :class:`BranchPolicyResult` — re-export from
  :mod:`backend.app.schemas.branching`.
* :class:`MultiverseSnapshot` — input dataclass for the policy gate.
* :class:`GodReviewInput` — input dataclass for the God reviewer.

B4-B (branch-engine, lineage cache, prune helpers) is wired in defensively
below — its symbols are exported only when the corresponding modules exist.
"""
from __future__ import annotations

from backend.app.branching.branch_policy import (
    MultiverseSnapshot,
    evaluate_branch_policy,
)
from backend.app.branching.divergence import compute_divergence_estimate
from backend.app.branching.god_agent import GodReviewInput, god_review
from backend.app.branching.lineage_helpers import (
    compute_branch_depth,
    make_lineage_path,
)
from backend.app.schemas.branching import BranchPolicy, BranchPolicyResult

__all__ = [
    "god_review",
    "GodReviewInput",
    "BranchPolicy",
    "BranchPolicyResult",
    "evaluate_branch_policy",
    "MultiverseSnapshot",
    "compute_divergence_estimate",
    "make_lineage_path",
    "compute_branch_depth",
]


# ---------------------------------------------------------------------------
# B4-B optional re-exports — present once branch_engine / lineage / prune land.
# ---------------------------------------------------------------------------

try:  # pragma: no cover — depends on B4-B's progress
    from backend.app.branching.branch_engine import (  # noqa: F401
        BranchCommitResult,
        commit_branch,
    )

    __all__ += ["BranchCommitResult", "commit_branch"]
except Exception:  # noqa: BLE001
    pass

try:  # pragma: no cover — depends on B4-B's progress
    from backend.app.branching.delta import apply_delta  # noqa: F401

    __all__ += ["apply_delta"]
except Exception:  # noqa: BLE001
    pass

try:  # pragma: no cover — depends on B4-B's progress
    from backend.app.branching.lineage import (  # noqa: F401
        LineageCache,
        build_tree,
        get_descendants,
        get_lineage,
    )

    __all__ += ["LineageCache", "build_tree", "get_descendants", "get_lineage"]
except Exception:  # noqa: BLE001
    pass

try:  # pragma: no cover — depends on B4-B's progress
    from backend.app.branching.prune import prune_low_value  # noqa: F401

    __all__ += ["prune_low_value"]
except Exception:  # noqa: BLE001
    pass
