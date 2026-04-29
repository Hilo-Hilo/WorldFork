"""runtime execution metadata

Revision ID: 0002_runtime_execution_metadata
Revises: 0001_initial
Create Date: 2026-04-29
"""
from alembic import op
from sqlalchemy import inspect

from app.db.models import Base

revision = "0002_runtime_execution_metadata"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_RUNTIME_TABLES = [
    "tick_executions",
    "execution_nodes",
    "tick_checkpoints",
    "node_attempts",
    "interventions",
    "replay_sessions",
    "operation_logs",
]



def upgrade() -> None:
    bind = op.get_bind()
    for table_name in _RUNTIME_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)



def downgrade() -> None:
    bind = op.get_bind()
    if _is_offline_bind(bind):
        cascade = " CASCADE" if bind.dialect.name == "postgresql" else ""
        for table_name in reversed(_RUNTIME_TABLES):
            op.execute(f'DROP TABLE IF EXISTS "{table_name}"{cascade}')
        return

    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    for table_name in reversed(_RUNTIME_TABLES):
        if table_name in existing_tables:
            op.drop_table(table_name)



def _is_offline_bind(bind) -> bool:
    return bind.__class__.__name__ == "MockConnection"
