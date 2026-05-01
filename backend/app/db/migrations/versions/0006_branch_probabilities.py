"""branch and path probabilities

Revision ID: 0006_branch_probabilities
Revises: 0005_endpoint_ledgers
Create Date: 2026-05-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.db.models import JSONValue

revision = "0006_branch_probabilities"
down_revision = "0005_endpoint_ledgers"
branch_labels = None
depends_on = None


def _column_names(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()

    multiverse_columns = _column_names(bind, "multiverses")
    with op.batch_alter_table("multiverses") as batch_op:
        if "branch_probability" not in multiverse_columns:
            batch_op.add_column(
                sa.Column("branch_probability", sa.Numeric(12, 10), nullable=False, server_default="1.0")
            )
        if "path_probability" not in multiverse_columns:
            batch_op.add_column(
                sa.Column("path_probability", sa.Numeric(12, 10), nullable=False, server_default="1.0")
            )

    edge_columns = _column_names(bind, "multiverse_lineage_edges")
    with op.batch_alter_table("multiverse_lineage_edges") as batch_op:
        if "branch_probability" not in edge_columns:
            batch_op.add_column(
                sa.Column("branch_probability", sa.Numeric(12, 10), nullable=False, server_default="1.0")
            )
        if "parent_path_probability" not in edge_columns:
            batch_op.add_column(
                sa.Column("parent_path_probability", sa.Numeric(12, 10), nullable=False, server_default="1.0")
            )
        if "child_path_probability" not in edge_columns:
            batch_op.add_column(
                sa.Column("child_path_probability", sa.Numeric(12, 10), nullable=False, server_default="1.0")
            )
        if "probability_basis" not in edge_columns:
            batch_op.add_column(sa.Column("probability_basis", JSONValue(), nullable=False, server_default="{}"))

    # Keep defaults ORM-owned after the backfill.
    with op.batch_alter_table("multiverses") as batch_op:
        if "branch_probability" not in multiverse_columns:
            batch_op.alter_column("branch_probability", server_default=None)
        if "path_probability" not in multiverse_columns:
            batch_op.alter_column("path_probability", server_default=None)
    with op.batch_alter_table("multiverse_lineage_edges") as batch_op:
        if "branch_probability" not in edge_columns:
            batch_op.alter_column("branch_probability", server_default=None)
        if "parent_path_probability" not in edge_columns:
            batch_op.alter_column("parent_path_probability", server_default=None)
        if "child_path_probability" not in edge_columns:
            batch_op.alter_column("child_path_probability", server_default=None)
        if "probability_basis" not in edge_columns:
            batch_op.alter_column("probability_basis", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()

    edge_columns = _column_names(bind, "multiverse_lineage_edges")
    with op.batch_alter_table("multiverse_lineage_edges") as batch_op:
        for column_name in (
            "probability_basis",
            "child_path_probability",
            "parent_path_probability",
            "branch_probability",
        ):
            if column_name in edge_columns:
                batch_op.drop_column(column_name)

    multiverse_columns = _column_names(bind, "multiverses")
    with op.batch_alter_table("multiverses") as batch_op:
        for column_name in ("path_probability", "branch_probability"):
            if column_name in multiverse_columns:
                batch_op.drop_column(column_name)
