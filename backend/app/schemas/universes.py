"""
Universe-level schemas: BigBangRun and Universe.
Import-free of backend.app.models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.app.schemas.common import RunStatus, UniverseStatus

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# ---------------------------------------------------------------------------
# BigBangRun
# ---------------------------------------------------------------------------

class BigBangRun(BaseModel):
    """Root run manifest — verbatim from the current schema."""

    model_config = ConfigDict(extra="forbid")

    big_bang_id: NonEmptyStr
    display_name: NonEmptyStr
    created_at: datetime
    updated_at: datetime
    created_by_user_id: NonEmptyStr | None = None
    scenario_text: NonEmptyStr
    input_file_ids: list[NonEmptyStr] = Field(default_factory=list)
    status: RunStatus
    time_horizon_label: NonEmptyStr
    tick_duration_minutes: int = Field(..., gt=0)
    max_ticks: int = Field(..., gt=0)
    max_schedule_horizon_ticks: int = Field(..., gt=0)
    source_of_truth_version: NonEmptyStr
    source_of_truth_snapshot_path: NonEmptyStr
    provider_snapshot_id: NonEmptyStr
    root_universe_id: NonEmptyStr
    run_folder_path: NonEmptyStr
    safe_edit_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

class Universe(BaseModel):
    """A single timeline branch — verbatim from the current schema with added validators."""

    model_config = ConfigDict(extra="forbid")

    universe_id: NonEmptyStr
    big_bang_id: NonEmptyStr
    parent_universe_id: NonEmptyStr | None = None
    child_universe_ids: list[NonEmptyStr] = Field(default_factory=list)
    branch_from_tick: int = Field(..., ge=0)
    branch_depth: int = Field(..., ge=0)
    lineage_path: list[NonEmptyStr] = Field(..., min_length=1)
    status: UniverseStatus
    branch_reason: str = ""
    branch_delta: dict[str, Any] | None = None
    current_tick: int = Field(..., ge=0)
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    frozen_at: datetime | None = None
    killed_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_lineage_invariants(self) -> Universe:
        # lineage_path[-1] must equal universe_id
        if self.lineage_path[-1] != self.universe_id:
            raise ValueError(
                f"lineage_path[-1] must equal universe_id: "
                f"got {self.lineage_path[-1]!r} vs {self.universe_id!r}"
            )

        # branch_depth == len(lineage_path) - 1
        expected_depth = len(self.lineage_path) - 1
        if self.branch_depth != expected_depth:
            raise ValueError(
                f"branch_depth must equal len(lineage_path) - 1: "
                f"expected {expected_depth}, got {self.branch_depth}"
            )

        # parent_universe_id is None  ⟺  branch_depth == 0
        if self.parent_universe_id is None and self.branch_depth != 0:
            raise ValueError(
                "parent_universe_id is None implies branch_depth == 0"
            )
        if self.parent_universe_id is not None and self.branch_depth == 0:
            raise ValueError(
                "branch_depth == 0 implies parent_universe_id must be None"
            )

        # branch_from_tick >= 0 already enforced by Field; extra check for non-root
        # (root universes should have branch_from_tick == 0, which is valid >= 0)

        # Timestamp presence checks per status
        status = self.status
        if status == "frozen" and self.frozen_at is None:
            raise ValueError("frozen_at must be set when status is 'frozen'")
        if status == "killed" and self.killed_at is None:
            raise ValueError("killed_at must be set when status is 'killed'")
        if status == "completed" and self.completed_at is None:
            raise ValueError("completed_at must be set when status is 'completed'")
        # 'candidate' — timestamp fields may all be None (branch not yet promoted)
        # 'terminated' and 'merged' have no specific timestamp field in schema; allow None

        return self
