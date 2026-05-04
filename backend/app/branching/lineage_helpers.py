"""Lineage path helpers for the recursive multiverse DAG (multiverse lineage model).

Lineage paths are ordered lists of universe IDs from the root Big Bang
universe down to (and including) the universe being described.  They are stored
denormalized on :class:`backend.app.models.branches.BranchNodeModel` to make
ancestor/descendant queries cheap (no recursive CTEs needed for the common
"render the multiverse tree" view).

These helpers are pure functions — they do not touch the DB or the engine.
"""
from __future__ import annotations


def make_lineage_path(
    parent_lineage_path: list[str] | None,
    child_id: str,
) -> list[str]:
    """Return the lineage path for a child universe.

    * Root universe (parent_lineage_path is ``None`` or empty) → ``[child_id]``.
    * Otherwise → ``[*parent_lineage_path, child_id]``.

    The returned list is always a fresh list, never a reference to the input,
    so callers can safely mutate it without aliasing the parent's path.
    """
    if not parent_lineage_path:
        return [child_id]
    return [*parent_lineage_path, child_id]


def compute_branch_depth(lineage_path: list[str]) -> int:
    """Return the branch depth — root has depth 0, each level adds 1.

    Defined as ``max(0, len(lineage_path) - 1)``.  An empty path returns 0
    (defensive — should never happen for a real universe but we don't want to
    surface a negative depth into the schema, which has ``ge=0``).
    """
    if not lineage_path:
        return 0
    return max(0, len(lineage_path) - 1)


__all__ = ["make_lineage_path", "compute_branch_depth"]
