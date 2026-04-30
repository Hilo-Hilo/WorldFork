"""structured report versions and multiverse continuation metadata

Revision ID: 0004_structured_report_versions
Revises: 0003_job_queue_control_plane
Create Date: 2026-04-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.models import GUID

revision = "0004_structured_report_versions"
down_revision = "0003_job_queue_control_plane"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _foreign_key_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {fk["name"] for fk in inspector.get_foreign_keys(table_name) if fk.get("name")}


def _has_foreign_key(bind, table_name: str, constrained_columns: list[str], referred_table: str) -> bool:
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys(table_name):
        if fk.get("constrained_columns") == constrained_columns and fk.get("referred_table") == referred_table:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()

    multiverse_columns = _column_names(bind, "multiverses")
    with op.batch_alter_table("multiverses") as batch_op:
        if "version" not in multiverse_columns:
            batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        if "ended_at" not in multiverse_columns:
            batch_op.add_column(sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
        if "continued_from_report_version_id" not in multiverse_columns:
            batch_op.add_column(sa.Column("continued_from_report_version_id", GUID(), nullable=True))

    if "version" not in multiverse_columns:
        with op.batch_alter_table("multiverses") as batch_op:
            batch_op.alter_column("version", server_default=None)

    multiverse_fks = _foreign_key_names(bind, "multiverses")
    with op.batch_alter_table("multiverses") as batch_op:
        if (
            "fk_multiverses_continued_from_report_version_id_report_versions" not in multiverse_fks
            and not _has_foreign_key(bind, "multiverses", ["continued_from_report_version_id"], "report_versions")
        ):
            batch_op.create_foreign_key(
                "fk_multiverses_continued_from_report_version_id_report_versions",
                "report_versions",
                ["continued_from_report_version_id"],
                ["id"],
            )

    report_version_columns = _column_names(bind, "report_versions")
    with op.batch_alter_table("report_versions") as batch_op:
        if "source_multiverse_version" not in report_version_columns:
            batch_op.add_column(sa.Column("source_multiverse_version", sa.Integer(), nullable=True))
        if "source_big_bang_config_version" not in report_version_columns:
            batch_op.add_column(sa.Column("source_big_bang_config_version", sa.Integer(), nullable=True))
        if "source_tick_snapshot_id" not in report_version_columns:
            batch_op.add_column(sa.Column("source_tick_snapshot_id", GUID(), nullable=True))
        if "source_tick_index" not in report_version_columns:
            batch_op.add_column(sa.Column("source_tick_index", sa.Integer(), nullable=True))
        if "source_multiverse_ids" not in report_version_columns:
            batch_op.add_column(sa.Column("source_multiverse_ids", sa.JSON(), nullable=True))
        if "content" not in report_version_columns:
            batch_op.add_column(sa.Column("content", sa.JSON(), nullable=True))
        if "generation_metadata" not in report_version_columns:
            batch_op.add_column(sa.Column("generation_metadata", sa.JSON(), nullable=True))
        if "model" not in report_version_columns:
            batch_op.add_column(sa.Column("model", sa.String(length=160), nullable=True))

    report_version_fks = _foreign_key_names(bind, "report_versions")
    with op.batch_alter_table("report_versions") as batch_op:
        if (
            "fk_report_versions_source_tick_snapshot_id_tick_snapshots" not in report_version_fks
            and not _has_foreign_key(bind, "report_versions", ["source_tick_snapshot_id"], "tick_snapshots")
        ):
            batch_op.create_foreign_key(
                "fk_report_versions_source_tick_snapshot_id_tick_snapshots",
                "tick_snapshots",
                ["source_tick_snapshot_id"],
                ["id"],
            )


def downgrade() -> None:
    bind = op.get_bind()

    report_version_columns = _column_names(bind, "report_versions")
    report_version_fks = _foreign_key_names(bind, "report_versions")
    with op.batch_alter_table("report_versions") as batch_op:
        if "fk_report_versions_source_tick_snapshot_id_tick_snapshots" in report_version_fks:
            batch_op.drop_constraint(
                "fk_report_versions_source_tick_snapshot_id_tick_snapshots",
                type_="foreignkey",
            )
    with op.batch_alter_table("report_versions") as batch_op:
        for column_name in (
            "model",
            "generation_metadata",
            "content",
            "source_multiverse_ids",
            "source_tick_index",
            "source_tick_snapshot_id",
            "source_big_bang_config_version",
            "source_multiverse_version",
        ):
            if column_name in report_version_columns:
                batch_op.drop_column(column_name)

    multiverse_columns = _column_names(bind, "multiverses")
    multiverse_fks = _foreign_key_names(bind, "multiverses")
    with op.batch_alter_table("multiverses") as batch_op:
        if "fk_multiverses_continued_from_report_version_id_report_versions" in multiverse_fks:
            batch_op.drop_constraint(
                "fk_multiverses_continued_from_report_version_id_report_versions",
                type_="foreignkey",
            )
    with op.batch_alter_table("multiverses") as batch_op:
        for column_name in ("continued_from_report_version_id", "ended_at", "version"):
            if column_name in multiverse_columns:
                batch_op.drop_column(column_name)
