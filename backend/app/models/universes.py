"""
UniverseModel — mirrors Universe.
Table: universes
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.schemas.universes import Universe


class UniverseModel(Base):
    __tablename__ = "universes"

    universe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    big_bang_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("big_bang_runs.big_bang_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_universe_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="SET NULL"),
        nullable=True,
    )
    lineage_path: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False
    )
    branch_from_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    branch_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    branch_delta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    current_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    killed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    run: Mapped[BigBangRunModel] = relationship(  # noqa: F821
        "BigBangRunModel",
        back_populates="universes",
        foreign_keys=[big_bang_id],
        lazy="noload",
    )
    parent: Mapped[UniverseModel | None] = relationship(
        "UniverseModel",
        remote_side="UniverseModel.universe_id",
        foreign_keys=[parent_universe_id],
        back_populates="children",
        lazy="noload",
    )
    children: Mapped[list[UniverseModel]] = relationship(
        "UniverseModel",
        foreign_keys=[parent_universe_id],
        back_populates="parent",
        lazy="noload",
    )

    __table_args__ = (
        Index("ix_universes_big_bang_parent", "big_bang_id", "parent_universe_id"),
        Index("ix_universes_big_bang_status", "big_bang_id", "status"),
        Index(
            "ix_universes_lineage_path",
            "lineage_path",
            postgresql_using="gin",
        ),
    )

    # ------------------------------------------------------------------
    # Schema translation helpers
    # child_universe_ids is derived at query time via relationship
    # ------------------------------------------------------------------

    def to_schema(self) -> Universe:
        from backend.app.schemas.universes import Universe

        child_ids = [c.universe_id for c in (self.children or [])]
        return Universe(
            universe_id=self.universe_id,
            big_bang_id=self.big_bang_id,
            parent_universe_id=self.parent_universe_id,
            child_universe_ids=child_ids,
            branch_from_tick=self.branch_from_tick if self.branch_from_tick is not None else 0,
            branch_depth=self.branch_depth,
            lineage_path=list(self.lineage_path),
            status=self.status,  # type: ignore[arg-type]
            branch_reason=self.branch_reason,
            branch_delta=dict(self.branch_delta) if self.branch_delta else None,
            current_tick=self.current_tick,
            latest_metrics=dict(self.latest_metrics or {}),
            created_at=self.created_at,
            frozen_at=self.frozen_at,
            killed_at=self.killed_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_schema(cls, s: Universe) -> UniverseModel:
        return cls(
            universe_id=s.universe_id,
            big_bang_id=s.big_bang_id,
            parent_universe_id=s.parent_universe_id,
            lineage_path=list(s.lineage_path),
            branch_from_tick=s.branch_from_tick,
            branch_depth=s.branch_depth,
            status=s.status,
            branch_reason=s.branch_reason,
            branch_delta=dict(s.branch_delta) if s.branch_delta else None,
            current_tick=s.current_tick,
            latest_metrics=dict(s.latest_metrics),
            created_at=s.created_at,
            frozen_at=s.frozen_at,
            killed_at=s.killed_at,
            completed_at=s.completed_at,
        )


# Avoid circular import at module level
from backend.app.models.runs import BigBangRunModel  # noqa: E402, F401
