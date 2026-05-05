"""
BigBangRunModel — mirrors BigBangRun.
Table: big_bang_runs
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.schemas.universes import BigBangRun


class BigBangRunModel(Base, TimestampMixin):
    __tablename__ = "big_bang_runs"

    big_bang_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    scenario_text: Mapped[str] = mapped_column(Text, nullable=False)
    input_file_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    time_horizon_label: Mapped[str] = mapped_column(String, nullable=False)
    tick_duration_minutes: Mapped[int] = mapped_column(nullable=False)
    max_ticks: Mapped[int] = mapped_column(nullable=False)
    max_schedule_horizon_ticks: Mapped[int] = mapped_column(nullable=False)
    source_of_truth_version: Mapped[str] = mapped_column(String, nullable=False)
    source_of_truth_snapshot_path: Mapped[str] = mapped_column(String, nullable=False)
    provider_snapshot_id: Mapped[str] = mapped_column(String, nullable=False)
    root_universe_id: Mapped[str] = mapped_column(String, nullable=False)
    run_folder_path: Mapped[str] = mapped_column(String, nullable=False)
    safe_edit_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    universes: Mapped[list[UniverseModel]] = relationship(  # noqa: F821
        "UniverseModel",
        back_populates="run",
        foreign_keys="UniverseModel.big_bang_id",
        lazy="noload",
    )

    # ------------------------------------------------------------------
    # Schema translation helpers
    # ------------------------------------------------------------------

    def to_schema(self) -> BigBangRun:
        from backend.app.schemas.universes import BigBangRun

        return BigBangRun(
            big_bang_id=self.big_bang_id,
            display_name=self.display_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
            created_by_user_id=self.created_by_user_id,
            scenario_text=self.scenario_text,
            input_file_ids=list(self.input_file_ids or []),
            status=self.status,  # type: ignore[arg-type]
            time_horizon_label=self.time_horizon_label,
            tick_duration_minutes=self.tick_duration_minutes,
            max_ticks=self.max_ticks,
            max_schedule_horizon_ticks=self.max_schedule_horizon_ticks,
            source_of_truth_version=self.source_of_truth_version,
            source_of_truth_snapshot_path=self.source_of_truth_snapshot_path,
            provider_snapshot_id=self.provider_snapshot_id,
            root_universe_id=self.root_universe_id,
            run_folder_path=self.run_folder_path,
            safe_edit_metadata=dict(self.safe_edit_metadata or {}),
        )

    @classmethod
    def from_schema(cls, s: BigBangRun) -> BigBangRunModel:
        return cls(
            big_bang_id=s.big_bang_id,
            display_name=s.display_name,
            created_at=s.created_at,
            updated_at=s.updated_at,
            created_by_user_id=s.created_by_user_id,
            scenario_text=s.scenario_text,
            input_file_ids=list(s.input_file_ids),
            status=s.status,
            time_horizon_label=s.time_horizon_label,
            tick_duration_minutes=s.tick_duration_minutes,
            max_ticks=s.max_ticks,
            max_schedule_horizon_ticks=s.max_schedule_horizon_ticks,
            source_of_truth_version=s.source_of_truth_version,
            source_of_truth_snapshot_path=s.source_of_truth_snapshot_path,
            provider_snapshot_id=s.provider_snapshot_id,
            root_universe_id=s.root_universe_id,
            run_folder_path=s.run_folder_path,
            safe_edit_metadata=dict(s.safe_edit_metadata),
        )


# Avoid circular import at module level
from backend.app.models.universes import UniverseModel  # noqa: E402, F401
