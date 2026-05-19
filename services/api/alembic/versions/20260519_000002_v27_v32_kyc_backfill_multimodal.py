"""Gap coverage v27–v32: KYC events, backfill tracking, cluster features,
multi-modal bets, report_packages status, REPORT_SUBMITTED event type.

Revision ID: 20260519_000002
Revises: 20260519_000001
Create Date: 2026-05-19 00:00:02

Todas as operações são idempotentes (IF NOT EXISTS / IF EXISTS / DO-blocks).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260519_000002"
down_revision = "20260519_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── v27: backfill tracking em ingest_jobs, alerts, cases ─────────────────
    conn.execute(sa.text("""
        ALTER TABLE ingest_jobs
            ADD COLUMN IF NOT EXISTS ingest_mode VARCHAR(20) NOT NULL DEFAULT 'incremental',
            ADD COLUMN IF NOT EXISTS is_backfill  BOOLEAN      NOT NULL DEFAULT FALSE;
    """))
    conn.execute(sa.text("""
        ALTER TABLE alerts
            ADD COLUMN IF NOT EXISTS ingest_mode     VARCHAR(20) NOT NULL DEFAULT 'incremental',
            ADD COLUMN IF NOT EXISTS backfill_job_id TEXT;
    """))
    conn.execute(sa.text("""
        ALTER TABLE cases
            ADD COLUMN IF NOT EXISTS backfill_job_id TEXT,
            ADD COLUMN IF NOT EXISTS ingest_mode     VARCHAR(20) NOT NULL DEFAULT 'incremental';
    """))

    # player_kyc_events — cria com player_id TEXT (compatível com v27.sql);
    # a migration 20260519_000003 irá upgradar para UUID FK com validação
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS player_kyc_events (
            id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            player_id     TEXT         NOT NULL,
            event_type    VARCHAR(40)  NOT NULL,
            provider      VARCHAR(40)  NOT NULL DEFAULT 'manual',
            status        VARCHAR(20)  NOT NULL DEFAULT 'PENDING',
            payload       JSONB        NOT NULL DEFAULT '{}',
            response      JSONB                 DEFAULT '{}',
            error_message TEXT,
            processed_at  TIMESTAMPTZ,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        );
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_player_kyc_events_tenant_player
            ON player_kyc_events (tenant_id, player_id, created_at);
    """))

    # ── v28: ML features / cluster em players ────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE players
            ADD COLUMN IF NOT EXISTS features     JSONB   NOT NULL DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS cluster_id   INTEGER,
            ADD COLUMN IF NOT EXISTS cluster_size INTEGER NOT NULL DEFAULT 0;
    """))

    # ── v29: campos multi-modal em bets (Lei 14.790 art. 3º) ─────────────────
    conn.execute(sa.text("""
        ALTER TABLE bets
            ADD COLUMN IF NOT EXISTS product_type  TEXT NOT NULL DEFAULT 'SPORTSBOOK',
            ADD COLUMN IF NOT EXISTS game_id       TEXT,
            ADD COLUMN IF NOT EXISTS game_name     TEXT,
            ADD COLUMN IF NOT EXISTS game_provider TEXT,
            ADD COLUMN IF NOT EXISTS game_category TEXT,
            ADD COLUMN IF NOT EXISTS rtp_teorico   NUMERIC(6, 4);
    """))

    # ── v30: REPORT_SUBMITTED em case_events ─────────────────────────────────
    # Adicionar via extensão do check (recria o constraint se necessário)
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'case_events' AND constraint_name = 'case_events_event_type_check'
            ) THEN
                ALTER TABLE case_events DROP CONSTRAINT IF EXISTS case_events_event_type_check;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        ALTER TABLE case_events
            ADD CONSTRAINT case_events_event_type_check
            CHECK (event_type IN (
                'COMMENT', 'STATUS_CHANGE', 'ALERT_LINKED', 'REPORT_GENERATED',
                'ASSIGNED', 'CLOSED', 'EVIDENCE_ADDED', 'REPORT_SUBMITTED'
            ));
    """))

    # ── v31: report_packages.status — revisão do fluxo ───────────────────────
    conn.execute(sa.text("""
        ALTER TABLE report_packages
            DROP CONSTRAINT IF EXISTS report_packages_status_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE report_packages
            ADD CONSTRAINT report_packages_status_check
            CHECK (status IN ('DRAFT','FINAL','PENDING_REVIEW','FILED','REJECTED','ARCHIVED'));
    """))

    # ── v32: external_validation_requests — criar se não existe ──────────────
    # (Pode já existir via Alembic 20260318_000005; esta instrução é no-op nesse caso)
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS external_validation_requests (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            player_id           UUID        NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            provider            VARCHAR(40) NOT NULL DEFAULT 'mock_identity',
            validation_type     VARCHAR(40) NOT NULL DEFAULT 'CPF_IDENTITY',
            status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            request_payload     JSONB       NOT NULL DEFAULT '{}',
            response_payload    JSONB       DEFAULT '{}',
            external_request_id TEXT,
            error_message       TEXT,
            requested_by        UUID        REFERENCES users(id),
            requested_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at        TIMESTAMPTZ,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """))
    # Adicionar FKs em bases onde a tabela foi criada via v32.sql (sem FKs)
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'external_validation_requests'
                  AND constraint_name = 'external_validation_requests_tenant_id_fkey'
            ) THEN
                ALTER TABLE external_validation_requests
                    ADD CONSTRAINT external_validation_requests_tenant_id_fkey
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'external_validation_requests'
                  AND constraint_name = 'external_validation_requests_player_id_fkey'
            ) THEN
                ALTER TABLE external_validation_requests
                    ADD CONSTRAINT external_validation_requests_player_id_fkey
                    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'external_validation_requests'
                  AND constraint_name = 'external_validation_requests_requested_by_fkey'
            ) THEN
                ALTER TABLE external_validation_requests
                    ADD CONSTRAINT external_validation_requests_requested_by_fkey
                    FOREIGN KEY (requested_by) REFERENCES users(id);
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_ext_validation_tenant_player
            ON external_validation_requests (tenant_id, player_id, requested_at);
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE bets DROP COLUMN IF EXISTS product_type, DROP COLUMN IF EXISTS game_id, DROP COLUMN IF EXISTS game_name, DROP COLUMN IF EXISTS game_provider, DROP COLUMN IF EXISTS game_category, DROP COLUMN IF EXISTS rtp_teorico"))
    conn.execute(sa.text("ALTER TABLE players DROP COLUMN IF EXISTS features, DROP COLUMN IF EXISTS cluster_id, DROP COLUMN IF EXISTS cluster_size"))
    conn.execute(sa.text("DROP TABLE IF EXISTS player_kyc_events"))
    conn.execute(sa.text("ALTER TABLE alerts DROP COLUMN IF EXISTS ingest_mode, DROP COLUMN IF EXISTS backfill_job_id"))
    conn.execute(sa.text("ALTER TABLE ingest_jobs DROP COLUMN IF EXISTS ingest_mode, DROP COLUMN IF EXISTS is_backfill"))
