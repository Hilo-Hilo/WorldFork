"""
BranchNodeModel — mirrors BranchNode.
Table: branch_nodes  (one-to-one with universes)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.schemas.branching import BranchNode


class BranchNodeModel(Base):
    __tablename__ = "branch_nodes"

    universe_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="CASCADE"),
        primary_key=True,
    )
    parent_universe_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_universe_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_point_id: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_trigger: Mapped[str] = mapped_column(Text, nullable=False)
    branch_delta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_estimate: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    descendant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    lineage_path: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    __table_args__ = (
        Index("ix_branch_nodes_parent_universe_id", "parent_universe_id"),
        Index(
            "ix_branch_nodes_lineage_path",
            "lineage_path",
            postgresql_using="gin",
        ),
    )

    def to_schema(self) -> BranchNode:
        from backend.app.schemas.branching import BranchNode

        return BranchNode(
            universe_id=self.universe_id,
            parent_universe_id=self.parent_universe_id,
            child_universe_ids=list(self.child_universe_ids or []),
            depth=self.depth,
            branch_tick=self.branch_tick,
            branch_point_id=self.branch_point_id,
            branch_trigger=self.branch_trigger,
            branch_delta=dict(self.branch_delta or {}),
            status=self.status,  # type: ignore[arg-type]
            metrics_summary=dict(self.metrics_summary or {}),
            cost_estimate=dict(self.cost_estimate or {}),
            descendant_count=self.descendant_count,
        )

    @classmethod
    def from_schema(cls, s: BranchNode) -> BranchNodeModel:
        return cls(
            universe_id=s.universe_id,
            parent_universe_id=s.parent_universe_id,
            child_universe_ids=list(s.child_universe_ids),
            depth=s.depth,
            branch_tick=s.branch_tick,
            branch_point_id=s.branch_point_id,
            branch_trigger=s.branch_trigger,
            branch_delta=dict(s.branch_delta),
            status=s.status,
            metrics_summary=dict(s.metrics_summary),
            cost_estimate=dict(s.cost_estimate),
            descendant_count=s.descendant_count,
            lineage_path=[],  # populated separately if needed
        )
