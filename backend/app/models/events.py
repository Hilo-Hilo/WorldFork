"""
EventModel — mirrors Event.
Table: events
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base

if TYPE_CHECKING:
    from backend.app.schemas.events import Event


class EventModel(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    universe_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="CASCADE"),
        nullable=False,
    )

    created_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ticks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    participants: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    target_audience: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    preconditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expected_effects: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    actual_effects: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_level: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_llm_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_events_universe_scheduled_status", "universe_id", "scheduled_tick", "status"),
        Index("ix_events_universe_status", "universe_id", "status"),
    )

    def to_schema(self) -> Event:
        from backend.app.schemas.events import Event

        return Event(
            event_id=self.event_id,
            universe_id=self.universe_id,
            created_tick=self.created_tick,
            scheduled_tick=self.scheduled_tick,
            duration_ticks=self.duration_ticks,
            event_type=self.event_type,
            title=self.title,
            description=self.description,
            created_by_actor_id=self.created_by_actor_id,
            participants=list(self.participants or []),
            target_audience=list(self.target_audience or []),
            visibility=self.visibility,
            preconditions=list(self.preconditions or []),
            expected_effects=dict(self.expected_effects or {}),
            actual_effects=dict(self.actual_effects) if self.actual_effects else None,
            risk_level=self.risk_level,
            status=self.status,  # type: ignore[arg-type]
            parent_event_id=self.parent_event_id,
            source_llm_call_id=self.source_llm_call_id,
        )

    @classmethod
    def from_schema(cls, s: Event) -> EventModel:
        return cls(
            event_id=s.event_id,
            universe_id=s.universe_id,
            created_tick=s.created_tick,
            scheduled_tick=s.scheduled_tick,
            duration_ticks=s.duration_ticks,
            event_type=s.event_type,
            title=s.title,
            description=s.description,
            created_by_actor_id=s.created_by_actor_id,
            participants=list(s.participants),
            target_audience=list(s.target_audience),
            visibility=s.visibility,
            preconditions=list(s.preconditions),
            expected_effects=dict(s.expected_effects),
            actual_effects=dict(s.actual_effects) if s.actual_effects else None,
            risk_level=s.risk_level,
            status=s.status,
            parent_event_id=s.parent_event_id,
            source_llm_call_id=s.source_llm_call_id,
        )
