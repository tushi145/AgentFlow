"""Add tasks.runtime_state (execution bookkeeping that survives approval pauses).

The column was added to the model in "Stage 2D" (commit 35e61c1). Databases created before that
never received it, because `create_all` only creates missing tables, never missing columns; any
INSERT into `tasks` then fails with "table tasks has no column named runtime_state".

Backwards compatible: the column is added with a server default of '{}', so existing rows get an
empty object (exactly what the application writes for new tasks) and older code that does not
know the column keeps working. Idempotent: databases created after Stage 2D already have the
column (with no server default) and are left as they are.

Revision ID: 0002_tasks_runtime_state
Revises: 0001_baseline
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_tasks_runtime_state"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _has_column("tasks", "runtime_state"):
        return
    op.add_column("tasks", sa.Column("runtime_state", sa.JSON(), nullable=False,
                                     server_default=sa.text("'{}'")))


def downgrade() -> None:
    if not _has_column("tasks", "runtime_state"):
        return
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("runtime_state")
