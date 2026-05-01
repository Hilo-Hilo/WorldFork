"""report evidence packs and timeline adjudication

Revision ID: 0007_report_adjudication_pack
Revises: 0006_branch_probabilities
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.models import GUID, JSONValue

revision = "0007_report_adjudication_pack"
down_revision = "0006_branch_probabilities"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _table_names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()

    endpoint_columns = _column_names(bind, "endpoint_ledger_entries")
    with op.batch_alter_table("endpoint_ledger_entries") as batch_op:
        if "realization_criteria" not in endpoint_columns:
            batch_op.add_column(sa.Column("realization_criteria", JSONValue(), nullable=False, server_default="[]"))
        if "negative_evidence_refs" not in endpoint_columns:
            batch_op.add_column(sa.Column("negative_evidence_refs", JSONValue(), nullable=False, server_default="[]"))
        if "status_basis" not in endpoint_columns:
            batch_op.add_column(sa.Column("status_basis", sa.Text(), nullable=True))

    with op.batch_alter_table("endpoint_ledger_entries") as batch_op:
        if "realization_criteria" not in endpoint_columns:
            batch_op.alter_column("realization_criteria", server_default=None)
        if "negative_evidence_refs" not in endpoint_columns:
            batch_op.alter_column("negative_evidence_refs", server_default=None)

    tables = _table_names(bind)
    if "timeline_adjudication_versions" not in tables:
        op.create_table(
            "timeline_adjudication_versions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("big_bang_id", GUID(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("source_type", sa.String(length=80), nullable=False),
            sa.Column("source_report_version_id", GUID(), nullable=True),
            sa.Column("parent_adjudication_version_id", GUID(), nullable=True),
            sa.Column("created_by", sa.String(length=120), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("payload", JSONValue(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["big_bang_id"], ["big_bangs.id"]),
            sa.ForeignKeyConstraint(["parent_adjudication_version_id"], ["timeline_adjudication_versions.id"]),
            sa.ForeignKeyConstraint(["source_report_version_id"], ["report_versions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("big_bang_id", "version", name="uq_timeline_adjudication_version"),
        )
        op.create_index(
            op.f("ix_timeline_adjudication_versions_big_bang_id"),
            "timeline_adjudication_versions",
            ["big_bang_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_versions_source_type"),
            "timeline_adjudication_versions",
            ["source_type"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_versions_status"),
            "timeline_adjudication_versions",
            ["status"],
            unique=False,
        )

    if "timeline_adjudication_entries" not in tables:
        op.create_table(
            "timeline_adjudication_entries",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("adjudication_version_id", GUID(), nullable=False),
            sa.Column("big_bang_id", GUID(), nullable=False),
            sa.Column("multiverse_id", GUID(), nullable=False),
            sa.Column("ui_label", sa.String(length=80), nullable=True),
            sa.Column("viability_status", sa.String(length=40), nullable=False),
            sa.Column("include_in_final", sa.Boolean(), nullable=False),
            sa.Column("prune_reason", sa.Text(), nullable=True),
            sa.Column("original_path_probability", sa.Numeric(12, 10), nullable=False),
            sa.Column("effective_path_probability", sa.Numeric(12, 10), nullable=False),
            sa.Column("mass_disposition", sa.String(length=80), nullable=False),
            sa.Column("endpoint_key", sa.String(length=160), nullable=True),
            sa.Column("endpoint_status", sa.String(length=40), nullable=True),
            sa.Column("evidence_summary", JSONValue(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["adjudication_version_id"], ["timeline_adjudication_versions.id"]),
            sa.ForeignKeyConstraint(["big_bang_id"], ["big_bangs.id"]),
            sa.ForeignKeyConstraint(["multiverse_id"], ["multiverses.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "adjudication_version_id",
                "multiverse_id",
                name="uq_timeline_adjudication_entry_multiverse",
            ),
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_adjudication_version_id"),
            "timeline_adjudication_entries",
            ["adjudication_version_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_big_bang_id"),
            "timeline_adjudication_entries",
            ["big_bang_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_multiverse_id"),
            "timeline_adjudication_entries",
            ["multiverse_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_viability_status"),
            "timeline_adjudication_entries",
            ["viability_status"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_include_in_final"),
            "timeline_adjudication_entries",
            ["include_in_final"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_endpoint_key"),
            "timeline_adjudication_entries",
            ["endpoint_key"],
            unique=False,
        )
        op.create_index(
            op.f("ix_timeline_adjudication_entries_endpoint_status"),
            "timeline_adjudication_entries",
            ["endpoint_status"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "timeline_adjudication_entries" in tables:
        op.drop_table("timeline_adjudication_entries")
    if "timeline_adjudication_versions" in tables:
        op.drop_table("timeline_adjudication_versions")

    endpoint_columns = _column_names(bind, "endpoint_ledger_entries")
    with op.batch_alter_table("endpoint_ledger_entries") as batch_op:
        for column_name in ("status_basis", "negative_evidence_refs", "realization_criteria"):
            if column_name in endpoint_columns:
                batch_op.drop_column(column_name)
