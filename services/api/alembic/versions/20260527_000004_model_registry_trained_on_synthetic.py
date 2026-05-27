"""Track synthetic model provenance in model_registry.

Revision ID: 20260527_000004
Revises: 20260526_000003
Create Date: 2026-05-27 00:04:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260527_000004"
down_revision = "20260526_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE model_registry
            ADD COLUMN IF NOT EXISTS trained_on_synthetic BOOLEAN NOT NULL DEFAULT FALSE;
    """))

    # Backfill from legacy JSONB markers.
    conn.execute(sa.text("""
        UPDATE model_registry
        SET trained_on_synthetic = TRUE
          WHERE LOWER(COALESCE(metrics ->> 'synthetic_bootstrap', 'false')) IN ('true', '1', 'yes', 'on')
              OR LOWER(COALESCE(metrics ->> 'synthetic', 'false')) IN ('true', '1', 'yes', 'on');
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE model_registry
            DROP COLUMN IF EXISTS trained_on_synthetic;
    """))
