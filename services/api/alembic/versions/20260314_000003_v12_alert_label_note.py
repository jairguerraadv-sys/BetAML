"""v12: alert label_note column

Revision ID: 20260314_000003
Revises: 20260314_000002
Create Date: 2026-03-14 00:00:03

Corresponds to: infra/migration_v12.sql
Adds analyst investigation notes to alert labels (LGPD compliance + feedback loop).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260314_000003"
down_revision = "20260314_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("label_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alerts", "label_note")
