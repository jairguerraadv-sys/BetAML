"""Add game_type to alerts + webhook_url/webhook_secret to tenants

Revision ID: 20260525_000001
Revises: 20260523_000001
Create Date: 2026-05-25 00:00:01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260525_000001"
down_revision = "20260523_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF to_regclass('public.alerts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'alerts' AND column_name = 'game_type'
               ) THEN
                ALTER TABLE alerts ADD COLUMN game_type VARCHAR(30);
            END IF;

            IF to_regclass('public.tenants') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'tenants' AND column_name = 'webhook_url'
               ) THEN
                ALTER TABLE tenants ADD COLUMN webhook_url VARCHAR(512);
                ALTER TABLE tenants ADD COLUMN webhook_secret VARCHAR(128);
            END IF;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'alerts' AND column_name = 'game_type'
            ) THEN
                ALTER TABLE alerts DROP COLUMN game_type;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'tenants' AND column_name = 'webhook_url'
            ) THEN
                ALTER TABLE tenants DROP COLUMN webhook_url;
                ALTER TABLE tenants DROP COLUMN webhook_secret;
            END IF;
        END $$;
    """))
