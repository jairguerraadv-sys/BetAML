"""v10: feature_version column on feature_snapshots

Revision ID: 20260314_000001
Revises: 20260313_000001
Create Date: 2026-03-14 00:00:01

Corresponds to: infra/migration_v10.sql
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260314_000001"
down_revision = "20260313_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_snapshots",
        sa.Column("feature_version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.create_index(
        "idx_feature_snapshots_version",
        "feature_snapshots",
        ["tenant_id", "player_id", "feature_version"],
    )


def downgrade() -> None:
    op.drop_index("idx_feature_snapshots_version", table_name="feature_snapshots")
    op.drop_column("feature_snapshots", "feature_version")
