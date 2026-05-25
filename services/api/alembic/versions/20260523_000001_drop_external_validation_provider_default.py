"""Drop legacy mock provider default from external validation requests.

Revision ID: 20260523_000001
Revises: 20260522_000007
Create Date: 2026-05-23 00:00:01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260523_000001"
down_revision = "20260522_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF to_regclass('public.external_validation_requests') IS NOT NULL THEN
                ALTER TABLE external_validation_requests
                    ALTER COLUMN provider DROP DEFAULT;
            END IF;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF to_regclass('public.external_validation_requests') IS NOT NULL THEN
                ALTER TABLE external_validation_requests
                    ALTER COLUMN provider SET DEFAULT 'mock_identity';
            END IF;
        END $$;
    """))