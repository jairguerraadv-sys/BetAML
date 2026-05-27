"""Add ML governance thresholds and manual-approval policy to scoring_configs.

Revision ID: 20260527_000005
Revises: 20260527_000004
Create Date: 2026-05-27 00:05:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260527_000005"
down_revision = "20260527_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE scoring_configs
            ADD COLUMN IF NOT EXISTS min_precision NUMERIC(5, 4) NOT NULL DEFAULT 0.80,
            ADD COLUMN IF NOT EXISTS max_false_positive_rate NUMERIC(5, 4) NOT NULL DEFAULT 0.20,
            ADD COLUMN IF NOT EXISTS min_recall NUMERIC(5, 4),
            ADD COLUMN IF NOT EXISTS require_manual_approval BOOLEAN NOT NULL DEFAULT TRUE;
    """))

    conn.execute(sa.text("""
        UPDATE scoring_configs
           SET min_precision = COALESCE(min_precision, 0.80),
               max_false_positive_rate = COALESCE(max_false_positive_rate, 0.20),
               require_manual_approval = COALESCE(require_manual_approval, TRUE)
         WHERE min_precision IS NULL
            OR max_false_positive_rate IS NULL
            OR require_manual_approval IS NULL;
    """))

    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'ck_scoring_configs_min_precision_range'
            ) THEN
                ALTER TABLE scoring_configs
                    ADD CONSTRAINT ck_scoring_configs_min_precision_range
                    CHECK (min_precision >= 0 AND min_precision <= 1);
            END IF;

            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'ck_scoring_configs_max_fpr_range'
            ) THEN
                ALTER TABLE scoring_configs
                    ADD CONSTRAINT ck_scoring_configs_max_fpr_range
                    CHECK (max_false_positive_rate >= 0 AND max_false_positive_rate <= 1);
            END IF;

            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'ck_scoring_configs_min_recall_range'
            ) THEN
                ALTER TABLE scoring_configs
                    ADD CONSTRAINT ck_scoring_configs_min_recall_range
                    CHECK (min_recall IS NULL OR (min_recall >= 0 AND min_recall <= 1));
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        ALTER TABLE scoring_configs
            DROP CONSTRAINT IF EXISTS ck_scoring_configs_min_recall_range,
            DROP CONSTRAINT IF EXISTS ck_scoring_configs_max_fpr_range,
            DROP CONSTRAINT IF EXISTS ck_scoring_configs_min_precision_range;
    """))

    conn.execute(sa.text("""
        ALTER TABLE scoring_configs
            DROP COLUMN IF EXISTS require_manual_approval,
            DROP COLUMN IF EXISTS min_recall,
            DROP COLUMN IF EXISTS max_false_positive_rate,
            DROP COLUMN IF EXISTS min_precision;
    """))
