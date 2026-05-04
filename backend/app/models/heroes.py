"""
HeroArchetypeModel and HeroStateModel.
Tables: hero_archetypes, hero_states
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.schemas.actors import HeroArchetype, HeroState


# ---------------------------------------------------------------------------
# HeroArchetypeModel
# ---------------------------------------------------------------------------

class HeroArchetypeModel(Base, TimestampMixin):
    __tablename__ = "hero_archetypes"

    hero_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    big_bang_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("big_bang_runs.big_bang_id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    institution: Mapped[str | None] = mapped_column(String, nullable=True)
    location_scope: Mapped[str] = mapped_column(String, nullable=False)

    public_reach: Mapped[float] = mapped_column(Float, nullable=False)
    institutional_power: Mapped[float] = mapped_column(Float, nullable=False)
    financial_power: Mapped[float] = mapped_column(Float, nullable=False)
    agenda_control: Mapped[float] = mapped_column(Float, nullable=False)
    media_access: Mapped[float] = mapped_column(Float, nullable=False)

    ideology_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    value_priors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    trust_priors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    behavioral_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    volatility: Mapped[float] = mapped_column(Float, nullable=False)
    ego_sensitivity: Mapped[float] = mapped_column(Float, nullable=False)
    strategic_discipline: Mapped[float] = mapped_column(Float, nullable=False)
    controversy_tolerance: Mapped[float] = mapped_column(Float, nullable=False)
    direct_event_power: Mapped[float] = mapped_column(Float, nullable=False)

    scheduling_permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    allowed_channels: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    def to_schema(self) -> HeroArchetype:
        from backend.app.schemas.actors import HeroArchetype

        return HeroArchetype(
            hero_id=self.hero_id,
            label=self.label,
            description=self.description,
            role=self.role,
            institution=self.institution,
            location_scope=self.location_scope,
            public_reach=self.public_reach,
            institutional_power=self.institutional_power,
            financial_power=self.financial_power,
            agenda_control=self.agenda_control,
            media_access=self.media_access,
            ideology_axes=dict(self.ideology_axes or {}),
            value_priors=dict(self.value_priors or {}),
            trust_priors=dict(self.trust_priors or {}),
            behavioral_axes=dict(self.behavioral_axes or {}),
            volatility=self.volatility,
            ego_sensitivity=self.ego_sensitivity,
            strategic_discipline=self.strategic_discipline,
            controversy_tolerance=self.controversy_tolerance,
            direct_event_power=self.direct_event_power,
            scheduling_permissions=list(self.scheduling_permissions or []),
            allowed_channels=list(self.allowed_channels or []),
        )

    @classmethod
    def from_schema(cls, s: HeroArchetype, big_bang_id: str) -> HeroArchetypeModel:
        return cls(
            hero_id=s.hero_id,
            big_bang_id=big_bang_id,
            label=s.label,
            description=s.description,
            role=s.role,
            institution=s.institution,
            location_scope=s.location_scope,
            public_reach=s.public_reach,
            institutional_power=s.institutional_power,
            financial_power=s.financial_power,
            agenda_control=s.agenda_control,
            media_access=s.media_access,
            ideology_axes=dict(s.ideology_axes),
            value_priors=dict(s.value_priors),
            trust_priors=dict(s.trust_priors),
            behavioral_axes=dict(s.behavioral_axes),
            volatility=s.volatility,
            ego_sensitivity=s.ego_sensitivity,
            strategic_discipline=s.strategic_discipline,
            controversy_tolerance=s.controversy_tolerance,
            direct_event_power=s.direct_event_power,
            scheduling_permissions=list(s.scheduling_permissions),
            allowed_channels=list(s.allowed_channels),
        )


# ---------------------------------------------------------------------------
# HeroStateModel
# ---------------------------------------------------------------------------

class HeroStateModel(Base):
    __tablename__ = "hero_states"

    hero_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("hero_archetypes.hero_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    universe_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="CASCADE"),
        nullable=False,
    )

    current_emotions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    current_issue_stances: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attention: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    perceived_pressure: Mapped[float] = mapped_column(Float, nullable=False)
    current_strategy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    queued_events: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    recent_posts: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    memory_session_id: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_hero_states_universe_tick", "universe_id", "tick"),
    )

    def to_schema(self) -> HeroState:
        from backend.app.schemas.actors import HeroState

        return HeroState(
            hero_id=self.hero_id,
            universe_id=self.universe_id,
            tick=self.tick,
            current_emotions=dict(self.current_emotions or {}),
            current_issue_stances=dict(self.current_issue_stances or {}),
            attention=self.attention,
            fatigue=self.fatigue,
            perceived_pressure=self.perceived_pressure,
            current_strategy=self.current_strategy,
            queued_events=list(self.queued_events or []),
            recent_posts=list(self.recent_posts or []),
            memory_session_id=self.memory_session_id,
        )

    @classmethod
    def from_schema(cls, s: HeroState) -> HeroStateModel:
        return cls(
            hero_id=s.hero_id,
            universe_id=s.universe_id,
            tick=s.tick,
            current_emotions=dict(s.current_emotions),
            current_issue_stances=dict(s.current_issue_stances),
            attention=s.attention,
            fatigue=s.fatigue,
            perceived_pressure=s.perceived_pressure,
            current_strategy=s.current_strategy,
            queued_events=list(s.queued_events),
            recent_posts=list(s.recent_posts),
            memory_session_id=s.memory_session_id,
        )
