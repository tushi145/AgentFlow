"""Baseline: the schema created by `Base.metadata.create_all` before Alembic was introduced.

Databases created before migrations existed have no `alembic_version` table. The migration runner
(`app.database.migrations`) stamps them at this revision and then upgrades, so every later
revision applies to them. Brand-new databases are created from the current models and stamped at
head (Alembic's "create_all + stamp" pattern), so this revision intentionally performs no DDL.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-18
"""

from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
