from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import CHAR, TypeDecorator


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if not isinstance(value, UUID):
            value = UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, UUID):
            return value
        return UUID(str(value))


class JSONValue(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_JSONB())
        return dialect.type_descriptor(JSON())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(GUID(), primary_key=True, default=uuid4)


class BigBang(Base, TimestampMixin):
    __tablename__ = "big_bangs"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    scenario_input: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    current_config_version: Mapped[int] = mapped_column(Integer, default=1)
    source_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("source_of_truth_snapshots.id"))

    configs: Mapped[list[BigBangConfig]] = relationship(back_populates="big_bang", foreign_keys="BigBangConfig.big_bang_id")
    multiverses: Mapped[list[Multiverse]] = relationship(back_populates="big_bang")


class BigBangConfig(Base, TimestampMixin):
    __tablename__ = "big_bang_configs"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    simulation_config: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    model_config: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    branch_policy: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))

    big_bang: Mapped[BigBang] = relationship(back_populates="configs", foreign_keys=[big_bang_id])


class BigBangConfigVersion(Base, TimestampMixin):
    __tablename__ = "big_bang_config_versions"
    __table_args__ = (UniqueConstraint("big_bang_id", "version", name="uq_big_bang_config_version"),)

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    simulation_config: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    model_config: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    branch_policy: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class SourceOfTruthSnapshot(Base, TimestampMixin):
    __tablename__ = "source_of_truth_snapshots"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    version: Mapped[str] = mapped_column(String(80), default="v1")
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    artifact_path: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(120))
    content_hash: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    debug_only: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class Actor(Base, TimestampMixin):
    __tablename__ = "actors"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    actor_type: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(240))
    description: Mapped[str | None] = mapped_column(Text)
    archetype: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    created_tick_index: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="active")


class PopulationArchetype(Base, TimestampMixin):
    __tablename__ = "population_archetypes"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    name: Mapped[str] = mapped_column(String(240))
    definition: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class Multiverse(Base, TimestampMixin):
    __tablename__ = "multiverses"
    __table_args__ = (UniqueConstraint("big_bang_id", "ui_label", name="uq_multiverse_label_per_big_bang"),)

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    parent_multiverse_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    fork_tick_index: Mapped[int | None] = mapped_column(Integer)
    ui_label: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    branch_reason: Mapped[str | None] = mapped_column(Text)
    state: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    report_status: Mapped[str] = mapped_column(String(40), default="not_ready")
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    continued_from_report_version_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("report_versions.id")
    )

    big_bang: Mapped[BigBang] = relationship(back_populates="multiverses")


class MultiverseLineageEdge(Base, TimestampMixin):
    __tablename__ = "multiverse_lineage_edges"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    parent_multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    child_multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    fork_tick_index: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)


class TickSnapshot(Base, TimestampMixin):
    __tablename__ = "tick_snapshots"
    __table_args__ = (UniqueConstraint("multiverse_id", "tick_index", name="uq_tick_per_multiverse"),)

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    ui_label: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), default="final")
    provisional_bundle: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    final_bundle: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    summary: Mapped[str | None] = mapped_column(Text)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), index=True)


class TickExecution(Base, TimestampMixin):
    __tablename__ = "tick_executions"
    __table_args__ = (
        CheckConstraint(
            "active_slot IS NULL OR active_slot = 'active'",
            name="ck_tick_execution_active_slot",
        ),
        UniqueConstraint(
            "multiverse_id",
            "tick_index",
            "active_slot",
            name="uq_tick_execution_active_slot",
        ),
        Index("ix_tick_executions_multiverse_tick", "multiverse_id", "tick_index"),
    )

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_snapshots.id"), index=True
    )
    queue_job_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("jobs.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    active_slot: Mapped[str | None] = mapped_column(String(20))
    requested_by: Mapped[str | None] = mapped_column(String(120))
    requested_reason: Mapped[str | None] = mapped_column(Text)
    runtime_meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    execution_nodes: Mapped[list[ExecutionNode]] = relationship(back_populates="tick_execution")
    checkpoints: Mapped[list[TickCheckpoint]] = relationship(back_populates="tick_execution")


class ExecutionNode(Base, TimestampMixin):
    __tablename__ = "execution_nodes"
    __table_args__ = (
        UniqueConstraint("tick_execution_id", "node_key", name="uq_execution_node_key"),
    )

    id: Mapped[UUID] = uuid_pk()
    tick_execution_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tick_executions.id"), index=True
    )
    parent_node_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("execution_nodes.id"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(160), index=True)
    node_kind: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    checkpoint_order: Mapped[int | None] = mapped_column(Integer)
    input_payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    output_payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tick_execution: Mapped[TickExecution] = relationship(back_populates="execution_nodes")
    node_attempts: Mapped[list[NodeAttempt]] = relationship(back_populates="execution_node")


class TickCheckpoint(Base, TimestampMixin):
    __tablename__ = "tick_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tick_execution_id", "checkpoint_key", name="uq_tick_checkpoint_key"
        ),
        UniqueConstraint(
            "tick_execution_id", "checkpoint_order", name="uq_tick_checkpoint_order"
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tick_execution_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tick_executions.id"), index=True
    )
    execution_node_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("execution_nodes.id"), index=True
    )
    checkpoint_key: Mapped[str] = mapped_column(String(160), index=True)
    checkpoint_order: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    raw_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    validated_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("artifacts.id")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tick_execution: Mapped[TickExecution] = relationship(back_populates="checkpoints")


class NodeAttempt(Base, TimestampMixin):
    __tablename__ = "node_attempts"
    __table_args__ = (
        UniqueConstraint("execution_node_id", "attempt_number", name="uq_node_attempt_number"),
    )

    id: Mapped[UUID] = uuid_pk()
    execution_node_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("execution_nodes.id"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    request_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    response_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    validated_artifact_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("artifacts.id")
    )
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    execution_node: Mapped[ExecutionNode] = relationship(back_populates="node_attempts")


class Intervention(Base, TimestampMixin):
    __tablename__ = "interventions"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_execution_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_executions.id"), index=True
    )
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_snapshots.id"), index=True
    )
    checkpoint_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_checkpoints.id"), index=True
    )
    node_attempt_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("node_attempts.id"), index=True
    )
    intervention_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="applied", index=True)
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    provenance: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class ReplaySession(Base, TimestampMixin):
    __tablename__ = "replay_sessions"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_execution_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_executions.id"), index=True
    )
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_snapshots.id"), index=True
    )
    checkpoint_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_checkpoints.id"), index=True
    )
    node_attempt_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("node_attempts.id"), index=True
    )
    requested_by: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="requested", index=True)
    replay_mode: Mapped[str] = mapped_column(String(80), default="resume")
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class OperationLog(Base, TimestampMixin):
    __tablename__ = "operation_logs"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("multiverses.id"), index=True
    )
    tick_execution_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_executions.id"), index=True
    )
    execution_node_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("execution_nodes.id"), index=True
    )
    node_attempt_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("node_attempts.id"), index=True
    )
    checkpoint_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_checkpoints.id"), index=True
    )
    intervention_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("interventions.id"), index=True
    )
    replay_session_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("replay_sessions.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    level: Mapped[str] = mapped_column(String(20), default="info", index=True)
    body: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    happened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class TickLineageRef(Base, TimestampMixin):
    __tablename__ = "tick_lineage_refs"
    __table_args__ = (UniqueConstraint("child_multiverse_id", "inherited_tick_index", name="uq_inherited_tick_ref"),)

    id: Mapped[UUID] = uuid_pk()
    child_multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    source_multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    source_tick_snapshot_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    inherited_tick_index: Mapped[int] = mapped_column(Integer)
    inherited_ui_label: Mapped[str] = mapped_column(String(120))


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    current_revision_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("event_revisions.id"))
    creator_actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    created_tick: Mapped[int] = mapped_column(Integer)
    scheduled_tick: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    expected_impact: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    actual_impact: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class EventRevision(Base, TimestampMixin):
    __tablename__ = "event_revisions"
    __table_args__ = (UniqueConstraint("event_id", "revision_number", name="uq_event_revision_number"),)

    id: Mapped[UUID] = uuid_pk()
    event_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    edited_by_actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    edited_by_agent_type: Mapped[str | None] = mapped_column(String(80))
    edit_reason: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    scheduled_tick: Mapped[int] = mapped_column(Integer)
    preconditions: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    expected_impact: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class EventLog(Base, TimestampMixin):
    __tablename__ = "event_logs"

    id: Mapped[UUID] = uuid_pk()
    event_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    log_type: Mapped[str] = mapped_column(String(80))
    body: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class EventSummary(Base, TimestampMixin):
    __tablename__ = "event_summaries"
    __table_args__ = (UniqueConstraint("event_id", "version", name="uq_event_summary_version"),)

    id: Mapped[UUID] = uuid_pk()
    event_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("events.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    supersedes_event_summary_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("event_summaries.id"))


class SocialPost(Base, TimestampMixin):
    __tablename__ = "social_posts"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    channel: Mapped[str] = mapped_column(String(120), default="oasis")
    body: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class OASISAction(Base, TimestampMixin):
    __tablename__ = "oasis_actions"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    action_type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class ReasoningTrace(Base, TimestampMixin):
    __tablename__ = "reasoning_traces"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    agent_type: Mapped[str] = mapped_column(String(120))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    sanitized_prompt_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    sanitized_response_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    raw_prompt_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    raw_response_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class GodAgentReview(Base, TimestampMixin):
    __tablename__ = "god_agent_reviews"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    decision: Mapped[str] = mapped_column(String(120))
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4, asdecimal=False), default=1.0)
    input_summary: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    output: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class ToolCall(Base, TimestampMixin):
    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_tool_call_idempotency_key"),)

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    god_review_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("god_agent_reviews.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    arguments: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    result: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(180))


class CohortState(Base, TimestampMixin):
    __tablename__ = "cohort_states"
    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    queued_event_ids: Mapped[list] = mapped_column(JSONValue(), default=list)


class HeroArchetype(Base, TimestampMixin):
    __tablename__ = "hero_archetypes"
    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    name: Mapped[str] = mapped_column(String(240))
    definition: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class HeroState(Base, TimestampMixin):
    __tablename__ = "hero_states"
    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    queued_event_ids: Mapped[list] = mapped_column(JSONValue(), default=list)


class CandidateBase(TimestampMixin):
    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="candidate")
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class CohortSplitCandidate(Base, CandidateBase):
    __tablename__ = "cohort_split_candidates"


class CohortSplit(Base, CandidateBase):
    __tablename__ = "cohort_splits"


class CohortMergeCandidate(Base, CandidateBase):
    __tablename__ = "cohort_merge_candidates"


class CohortMergePlan(Base, CandidateBase):
    __tablename__ = "cohort_merge_plans"


class CohortMerge(Base, CandidateBase):
    __tablename__ = "cohort_merges"


class CohortEmergenceCandidate(Base, CandidateBase):
    __tablename__ = "cohort_emergence_candidates"


class CohortEmergence(Base, CandidateBase):
    __tablename__ = "cohort_emergences"


class EmotionObservation(Base, TimestampMixin):
    __tablename__ = "emotion_observations"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    emotion: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Numeric(6, 3, asdecimal=False))
    source: Mapped[str] = mapped_column(String(120))
    evidence: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class EmotionGraphSnapshot(Base, TimestampMixin):
    __tablename__ = "emotion_graph_snapshots"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    graph: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class GraphEdge(Base, TimestampMixin):
    __tablename__ = "graph_edges"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    source_actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    target_actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    layer: Mapped[str] = mapped_column(String(120), index=True)
    weight: Mapped[float | None] = mapped_column(Numeric(8, 4, asdecimal=False))
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class GraphSnapshot(Base, TimestampMixin):
    __tablename__ = "graph_snapshots"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    layer: Mapped[str] = mapped_column(String(120), index=True)
    graph: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))


class SociologySignal(Base, TimestampMixin):
    __tablename__ = "sociology_signals"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    tick_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("tick_snapshots.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    signal: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class SociologyPromptInfluence(Base, TimestampMixin):
    __tablename__ = "sociology_prompt_influences"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("actors.id"))
    tick_index: Mapped[int] = mapped_column(Integer, index=True)
    applies_to_tick_index: Mapped[int] = mapped_column(Integer, index=True)
    influence: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class Report(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    multiverse_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("multiverses.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        Index(
            "uq_report_per_multiverse_type",
            "multiverse_id",
            "report_type",
            unique=True,
            sqlite_where=multiverse_id.is_not(None),
            postgresql_where=multiverse_id.is_not(None),
        ),
        Index(
            "uq_final_report_per_big_bang_type",
            "big_bang_id",
            "report_type",
            unique=True,
            sqlite_where=multiverse_id.is_(None),
            postgresql_where=multiverse_id.is_(None),
        ),
    )


class ReportVersion(Base, TimestampMixin):
    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("report_id", "version", name="uq_report_version"),)

    id: Mapped[UUID] = uuid_pk()
    report_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("reports.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    source_multiverse_version: Mapped[int | None] = mapped_column(Integer)
    source_big_bang_config_version: Mapped[int | None] = mapped_column(Integer)
    source_tick_snapshot_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("tick_snapshots.id")
    )
    source_tick_index: Mapped[int | None] = mapped_column(Integer)
    source_multiverse_ids: Mapped[list | None] = mapped_column(JSONValue(), default=list)
    content: Mapped[dict | None] = mapped_column(JSONValue(), default=dict)
    generation_metadata: Mapped[dict | None] = mapped_column(JSONValue(), default=dict)
    model: Mapped[str | None] = mapped_column(String(160))
    markdown_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    pdf_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    supersedes_report_version_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("report_versions.id"))


class LLMCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    id: Mapped[UUID] = uuid_pk()
    big_bang_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    request_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    response_artifact_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("artifacts.id"))
    meta: Mapped[dict] = mapped_column(JSONValue(), default=dict)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_job_idempotency_key"),
        Index("ix_jobs_queue_name_status", "queue_name", "status"),
    )

    id: Mapped[UUID] = uuid_pk()
    job_type: Mapped[str] = mapped_column(String(120), index=True)
    queue_name: Mapped[str] = mapped_column(String(80), default="default", index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    big_bang_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("big_bangs.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    result: Mapped[dict] = mapped_column(JSONValue(), default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    concurrency_key: Mapped[str | None] = mapped_column(String(180), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(180))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupt_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    interrupted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
