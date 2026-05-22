"""Product gap fixes: alerts SLA/priority, KYC contract, status checks and feature upsert.

Revision ID: 20260519_000005
Revises: 20260519_000004
Create Date: 2026-05-19 00:05:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260519_000005"
down_revision = "20260519_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE alerts
            ADD COLUMN IF NOT EXISTS priority TEXT NOT NULL DEFAULT 'MEDIUM',
            ADD COLUMN IF NOT EXISTS sla_due_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS triage_note TEXT;
        ALTER TABLE alerts
            ALTER COLUMN alert_type TYPE TEXT;
        ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_alert_type_check;
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_alert_type_check
            CHECK (alert_type IN (
                'RULE', 'ANOMALY', 'COMPOSITE', 'ML_ANOMALY',
                'NETWORK', 'INCOME_INCOMPATIBILITY', 'AML_SUSPICIOUS', 'AML_HIGH_RISK'
            ));
        ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_status_check;
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_status_check
            CHECK (status IN (
                'OPEN', 'IN_REVIEW', 'CONFIRMED', 'DISMISSED',
                'FALSE_POSITIVE', 'CLOSED'
            ));
        ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_priority_check;
        ALTER TABLE alerts
            ADD CONSTRAINT alerts_priority_check
            CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL'));
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_alerts_tenant_priority_sla
            ON alerts (tenant_id, priority, sla_due_at)
            WHERE sla_due_at IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_alerts_triage_note_not_null
            ON alerts (triaged_at DESC)
            WHERE triage_note IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_alerts_triaged_by_status
            ON alerts (tenant_id, triaged_by, status)
            WHERE triaged_by IS NOT NULL;
    """))
    conn.execute(sa.text("""
        UPDATE alerts
        SET composite_score = COALESCE(
                composite_score,
                anomaly_score,
                CASE severity
                    WHEN 'CRITICAL' THEN 0.95
                    WHEN 'HIGH' THEN 0.75
                    WHEN 'MEDIUM' THEN 0.50
                    ELSE 0.25
                END
            )
        WHERE composite_score IS NULL;

        UPDATE cases c
        SET auto_created = TRUE,
            auto_created_reason = COALESCE(auto_created_reason, 'backfilled_from_critical_alert')
        WHERE COALESCE(c.auto_created, FALSE) = FALSE
          AND EXISTS (
              SELECT 1
              FROM alerts a
              WHERE a.case_id = c.id
                AND a.severity = 'CRITICAL'
          );
    """))

    conn.execute(sa.text("""
        DELETE FROM feature_snapshots a
        USING feature_snapshots b
        WHERE a.tenant_id = b.tenant_id
          AND a.player_id = b.player_id
          AND a.feature_date = b.feature_date
          AND (
              COALESCE(a.created_at, 'epoch'::timestamptz) < COALESCE(b.created_at, 'epoch'::timestamptz)
              OR (
                  COALESCE(a.created_at, 'epoch'::timestamptz) = COALESCE(b.created_at, 'epoch'::timestamptz)
                  AND a.id::text < b.id::text
              )
          );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_snapshots_tenant_player_date
            ON feature_snapshots (tenant_id, player_id, feature_date);
    """))

    conn.execute(sa.text("""
        ALTER TABLE case_events DROP CONSTRAINT IF EXISTS case_events_event_type_check;
        ALTER TABLE case_events
            ADD CONSTRAINT case_events_event_type_check
            CHECK (event_type IN (
                'NOTE', 'COMMENT', 'STATUS_CHANGE', 'ALERT_LINKED',
                'ALERT_LINKED_TO_EXISTING_CASE', 'AUTO_CREATED', 'AUTO_CREATED_FROM_ALERT',
                'REPORT_GENERATED', 'REPORT_SUBMITTED', 'ASSIGNED', 'ASSIGNMENT',
                'CLOSED', 'EVIDENCE_ADDED', 'EVIDENCE_UPLOAD'
            ));
    """))

    conn.execute(sa.text("""
        ALTER TABLE players DROP CONSTRAINT IF EXISTS players_status_check;
        ALTER TABLE players
            ADD CONSTRAINT players_status_check
            CHECK (status IN (
                'ACTIVE', 'INACTIVE', 'BLOCKED', 'PENDING',
                'SELF_EXCLUDED', 'PENDING_KYC', 'ERASED',
                'SUSPENDED', 'BLOCKED_BY_OPERATOR', 'REACTIVATED',
                'CLOSED_BY_PLAYER', 'CLOSED_BY_OPERATOR'
            ));
    """))

    conn.execute(sa.text("""
        ALTER TABLE player_kyc_events
            DROP CONSTRAINT IF EXISTS player_kyc_events_player_id_fkey;
        ALTER TABLE player_kyc_events
            ALTER COLUMN player_id TYPE TEXT USING player_id::text;
        ALTER TABLE player_kyc_events
            ADD COLUMN IF NOT EXISTS entity_type VARCHAR(40),
            ADD COLUMN IF NOT EXISTS subtype VARCHAR(60),
            ADD COLUMN IF NOT EXISTS event_type VARCHAR(80),
            ADD COLUMN IF NOT EXISTS provider TEXT,
            ADD COLUMN IF NOT EXISTS status VARCHAR(30),
            ADD COLUMN IF NOT EXISTS document_type TEXT,
            ADD COLUMN IF NOT EXISTS pep_flag BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS income_declared NUMERIC(18,2),
            ADD COLUMN IF NOT EXISTS exclusion_source TEXT,
            ADD COLUMN IF NOT EXISTS exclusion_scope TEXT,
            ADD COLUMN IF NOT EXISTS exclusion_duration_days INTEGER,
            ADD COLUMN IF NOT EXISTS old_deposit_limit NUMERIC(18,2),
            ADD COLUMN IF NOT EXISTS new_deposit_limit NUMERIC(18,2),
            ADD COLUMN IF NOT EXISTS previous_status TEXT,
            ADD COLUMN IF NOT EXISTS new_status TEXT,
            ADD COLUMN IF NOT EXISTS reason TEXT,
            ADD COLUMN IF NOT EXISTS payload JSONB DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS response JSONB DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS error_message TEXT,
            ADD COLUMN IF NOT EXISTS ingest_mode VARCHAR(20) NOT NULL DEFAULT 'incremental',
            ADD COLUMN IF NOT EXISTS backfill_job_id TEXT,
            ADD COLUMN IF NOT EXISTS occurred_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ;
        UPDATE player_kyc_events
           SET entity_type = COALESCE(entity_type, 'KYC_EVENT'),
               subtype = COALESCE(subtype, event_type, 'MANUAL'),
               event_type = COALESCE(event_type, subtype, 'MANUAL'),
               status = COALESCE(status, 'PENDING'),
               occurred_at = COALESCE(occurred_at, created_at, NOW())
         WHERE entity_type IS NULL
            OR subtype IS NULL
            OR event_type IS NULL
            OR status IS NULL
            OR occurred_at IS NULL;
        ALTER TABLE player_kyc_events
            ALTER COLUMN entity_type SET NOT NULL,
            ALTER COLUMN subtype SET NOT NULL,
            ALTER COLUMN occurred_at SET NOT NULL;
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_player_kyc_events_tenant_player_created
            ON player_kyc_events (tenant_id, player_id, created_at DESC);
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS idx_alerts_tenant_priority_sla"))
    conn.execute(sa.text("DROP INDEX IF EXISTS idx_player_kyc_events_tenant_player_created"))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_feature_snapshots_tenant_player_date"))
