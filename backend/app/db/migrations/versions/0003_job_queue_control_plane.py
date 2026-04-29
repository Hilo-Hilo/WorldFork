"""Add DB-canonical queue control fields to jobs.

Revision ID: 0003_job_queue_control_plane
Revises: 0002_runtime_execution_metadata
Create Date: 2026-04-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_job_queue_control_plane"
down_revision = "0002_runtime_execution_metadata"
branch_labels = None
depends_on = None


def _column_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("jobs")}


def _index_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes("jobs")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    indexes = _index_names(bind)

    with op.batch_alter_table("jobs") as batch_op:
        if "queue_name" not in columns:
            batch_op.add_column(sa.Column("queue_name", sa.String(length=80), nullable=False, server_default="default"))
        if "concurrency_key" not in columns:
            batch_op.add_column(sa.Column("concurrency_key", sa.String(length=180), nullable=True))
        if "attempt_number" not in columns:
            batch_op.add_column(sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"))
        if "max_attempts" not in columns:
            batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        if "retryable" not in columns:
            batch_op.add_column(sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.true()))
        if "available_at" not in columns:
            batch_op.add_column(sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
        if "lease_owner" not in columns:
            batch_op.add_column(sa.Column("lease_owner", sa.String(length=180), nullable=True))
        if "lease_expires_at" not in columns:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        if "last_heartbeat_at" not in columns:
            batch_op.add_column(sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        if "queued_at" not in columns:
            batch_op.add_column(sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        if "started_at" not in columns:
            batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        if "paused_at" not in columns:
            batch_op.add_column(sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
        if "interrupt_requested_at" not in columns:
            batch_op.add_column(sa.Column("interrupt_requested_at", sa.DateTime(timezone=True), nullable=True))
        if "interrupted_at" not in columns:
            batch_op.add_column(sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True))
        if "finished_at" not in columns:
            batch_op.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE jobs
            SET queue_name = CASE
                WHEN job_type = 'initialize_big_bang' THEN 'big_bang_init'
                WHEN job_type IN ('run_multiverse_tick', 'simulate_multiverse_ticks') THEN 'multiverse_ticks'
                WHEN job_type IN ('generate_multiverse_report', 'generate_final_big_bang_report') THEN 'reports'
                WHEN job_type = 'run_big_bang_until_complete' THEN 'big_bang_control'
                ELSE COALESCE(queue_name, 'default')
            END
            """
        )
    )

    with op.batch_alter_table("jobs") as batch_op:
        if "queue_name" not in columns:
            batch_op.alter_column("queue_name", server_default=None)
        if "attempt_number" not in columns:
            batch_op.alter_column("attempt_number", server_default=None)
        if "max_attempts" not in columns:
            batch_op.alter_column("max_attempts", server_default=None)
        if "retryable" not in columns:
            batch_op.alter_column("retryable", server_default=None)

    if "ix_jobs_queue_name" not in indexes:
        op.create_index("ix_jobs_queue_name", "jobs", ["queue_name"], unique=False)
    if "ix_jobs_concurrency_key" not in indexes:
        op.create_index("ix_jobs_concurrency_key", "jobs", ["concurrency_key"], unique=False)
    if "ix_jobs_available_at" not in indexes:
        op.create_index("ix_jobs_available_at", "jobs", ["available_at"], unique=False)
    if "ix_jobs_lease_expires_at" not in indexes:
        op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"], unique=False)
    if "ix_jobs_queue_name_status" not in indexes:
        op.create_index("ix_jobs_queue_name_status", "jobs", ["queue_name", "status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    indexes = _index_names(bind)
    for index_name in (
        "ix_jobs_queue_name_status",
        "ix_jobs_lease_expires_at",
        "ix_jobs_available_at",
        "ix_jobs_concurrency_key",
        "ix_jobs_queue_name",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="jobs")

    columns = _column_names(bind)
    removable = [
        "finished_at",
        "interrupted_at",
        "interrupt_requested_at",
        "paused_at",
        "started_at",
        "queued_at",
        "last_heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "available_at",
        "retryable",
        "max_attempts",
        "attempt_number",
        "concurrency_key",
        "queue_name",
    ]
    with op.batch_alter_table("jobs") as batch_op:
        for column_name in removable:
            if column_name in columns:
                batch_op.drop_column(column_name)
