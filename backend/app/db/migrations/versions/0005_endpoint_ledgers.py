"""endpoint ledger versions and entries

Revision ID: 0005_endpoint_ledgers
Revises: 0004_structured_report_versions
Create Date: 2026-04-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.models import GUID, JSONValue

revision = "0005_endpoint_ledgers"
down_revision = "0004_structured_report_versions"
branch_labels = None
depends_on = None


def _table_names(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "endpoint_ledger_versions" not in tables:
        op.create_table(
            "endpoint_ledger_versions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("big_bang_id", GUID(), nullable=False),
            sa.Column("multiverse_id", GUID(), nullable=True),
            sa.Column("scope", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("source_type", sa.String(length=80), nullable=False),
            sa.Column("source_tick_snapshot_id", GUID(), nullable=True),
            sa.Column("source_report_version_id", GUID(), nullable=True),
            sa.Column("parent_ledger_version_id", GUID(), nullable=True),
            sa.Column("created_by", sa.String(length=120), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("model", sa.String(length=160), nullable=True),
            sa.Column("llm_call_id", GUID(), nullable=True),
            sa.Column("payload", JSONValue(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["big_bang_id"], ["big_bangs.id"]),
            sa.ForeignKeyConstraint(["multiverse_id"], ["multiverses.id"]),
            sa.ForeignKeyConstraint(["source_tick_snapshot_id"], ["tick_snapshots.id"]),
            sa.ForeignKeyConstraint(["source_report_version_id"], ["report_versions.id"]),
            sa.ForeignKeyConstraint(["parent_ledger_version_id"], ["endpoint_ledger_versions.id"]),
            sa.ForeignKeyConstraint(["llm_call_id"], ["llm_calls.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_endpoint_ledger_versions_big_bang_id", "endpoint_ledger_versions", ["big_bang_id"])
        op.create_index("ix_endpoint_ledger_versions_multiverse_id", "endpoint_ledger_versions", ["multiverse_id"])
        op.create_index("ix_endpoint_ledger_versions_scope", "endpoint_ledger_versions", ["scope"])
        op.create_index("ix_endpoint_ledger_versions_status", "endpoint_ledger_versions", ["status"])
        op.create_index("ix_endpoint_ledger_versions_source_type", "endpoint_ledger_versions", ["source_type"])
        op.create_index(
            "uq_endpoint_ledger_big_bang_scope_version",
            "endpoint_ledger_versions",
            ["big_bang_id", "scope", "version"],
            unique=True,
            sqlite_where=sa.text("multiverse_id IS NULL"),
            postgresql_where=sa.text("multiverse_id IS NULL"),
        )
        op.create_index(
            "uq_endpoint_ledger_multiverse_scope_version",
            "endpoint_ledger_versions",
            ["big_bang_id", "multiverse_id", "scope", "version"],
            unique=True,
            sqlite_where=sa.text("multiverse_id IS NOT NULL"),
            postgresql_where=sa.text("multiverse_id IS NOT NULL"),
        )

    tables = _table_names(bind)
    if "endpoint_ledger_entries" not in tables:
        op.create_table(
            "endpoint_ledger_entries",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("ledger_version_id", GUID(), nullable=False),
            sa.Column("endpoint_key", sa.String(length=160), nullable=False),
            sa.Column("label", sa.String(length=240), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("probability", sa.Numeric(precision=6, scale=5), nullable=True),
            sa.Column("authority_refs", JSONValue(), nullable=False),
            sa.Column("evidence_refs", JSONValue(), nullable=False),
            sa.Column("blockers", JSONValue(), nullable=False),
            sa.Column("contradiction_notes", sa.Text(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("last_observed_tick_index", sa.Integer(), nullable=True),
            sa.Column("meta", JSONValue(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["ledger_version_id"], ["endpoint_ledger_versions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ledger_version_id", "endpoint_key", name="uq_endpoint_ledger_entry_key"),
        )
        op.create_index("ix_endpoint_ledger_entries_ledger_version_id", "endpoint_ledger_entries", ["ledger_version_id"])
        op.create_index("ix_endpoint_ledger_entries_endpoint_key", "endpoint_ledger_entries", ["endpoint_key"])
        op.create_index("ix_endpoint_ledger_entries_status", "endpoint_ledger_entries", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = _table_names(bind)
    if "endpoint_ledger_entries" in tables:
        op.drop_index("ix_endpoint_ledger_entries_status", table_name="endpoint_ledger_entries")
        op.drop_index("ix_endpoint_ledger_entries_endpoint_key", table_name="endpoint_ledger_entries")
        op.drop_index("ix_endpoint_ledger_entries_ledger_version_id", table_name="endpoint_ledger_entries")
        op.drop_table("endpoint_ledger_entries")
    tables = _table_names(bind)
    if "endpoint_ledger_versions" in tables:
        op.drop_index("uq_endpoint_ledger_multiverse_scope_version", table_name="endpoint_ledger_versions")
        op.drop_index("uq_endpoint_ledger_big_bang_scope_version", table_name="endpoint_ledger_versions")
        op.drop_index("ix_endpoint_ledger_versions_source_type", table_name="endpoint_ledger_versions")
        op.drop_index("ix_endpoint_ledger_versions_status", table_name="endpoint_ledger_versions")
        op.drop_index("ix_endpoint_ledger_versions_scope", table_name="endpoint_ledger_versions")
        op.drop_index("ix_endpoint_ledger_versions_multiverse_id", table_name="endpoint_ledger_versions")
        op.drop_index("ix_endpoint_ledger_versions_big_bang_id", table_name="endpoint_ledger_versions")
        op.drop_table("endpoint_ledger_versions")
