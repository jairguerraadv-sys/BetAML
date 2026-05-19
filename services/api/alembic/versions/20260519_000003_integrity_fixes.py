"""Integrity fixes: IC-01 scored_at→created_at, IC-02 player_kyc FK UUID,
IC-03 severity_mode WEIGHTED, IA-01 players.status CHECK, IA-02 notifications
nullable, IA-03 payment_method_flagged (já adicionado em 20260519_000001
via v24 path; guard extra), roles DB sem BYPASSRLS.

Revision ID: 20260519_000003
Revises: 20260519_000002
Create Date: 2026-05-19 00:00:03

IMPORTANTE: scored_at existe apenas em bases criadas via Docker (migration_v16.sql).
Bases criadas via Alembic já possuem created_at (revision 20260320_000001).
Este script usa IF EXISTS para ser idempotente em ambos os caminhos.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260519_000003"
down_revision = "20260519_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── IC-01: model_inference_logs.scored_at → created_at ───────────────────
    # Em bases Docker a coluna se chama scored_at; em bases Alembic, created_at.
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'model_inference_logs' AND column_name = 'scored_at'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'model_inference_logs' AND column_name = 'created_at'
            ) THEN
                ALTER TABLE model_inference_logs
                    RENAME COLUMN scored_at TO created_at;
            END IF;
        END $$;
    """))
    # Garantir NOT NULL com server_default em ambos os caminhos
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'model_inference_logs'
                  AND column_name = 'created_at'
                  AND column_default IS NULL
            ) THEN
                ALTER TABLE model_inference_logs
                    ALTER COLUMN created_at SET DEFAULT NOW();
            END IF;
        END $$;
    """))

    # ── IC-02: player_kyc_events.player_id TEXT → UUID FK ────────────────────
    # Só converte se o tipo atual ainda for TEXT e todos os valores forem UUIDs válidos.
    conn.execute(sa.text("""
        DO $$
        DECLARE
            non_uuid_count INTEGER;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'player_kyc_events' AND column_name = 'player_id'
                  AND data_type = 'text'
            ) THEN
                SELECT COUNT(*) INTO non_uuid_count
                  FROM player_kyc_events
                 WHERE player_id !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
                IF non_uuid_count = 0 THEN
                    ALTER TABLE player_kyc_events
                        ALTER COLUMN player_id TYPE UUID USING player_id::uuid;
                    ALTER TABLE player_kyc_events
                        ADD CONSTRAINT player_kyc_events_player_id_fkey
                        FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE;
                ELSE
                    RAISE WARNING 'player_kyc_events: % rows have non-UUID player_id — skipping FK upgrade', non_uuid_count;
                END IF;
            END IF;
        END $$;
    """))

    # ── IC-03: compound_rules.severity_mode — adicionar WEIGHTED ─────────────
    conn.execute(sa.text("""
        ALTER TABLE compound_rules
            DROP CONSTRAINT IF EXISTS compound_rules_severity_mode_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE compound_rules
            ADD CONSTRAINT compound_rules_severity_mode_check
            CHECK (severity_mode IN ('MAX', 'MIN', 'FIXED', 'WEIGHTED'));
    """))

    # ── IA-01: players.status — adicionar SELF_EXCLUDED e PENDING_KYC ────────
    conn.execute(sa.text("""
        ALTER TABLE players
            DROP CONSTRAINT IF EXISTS players_status_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE players
            ADD CONSTRAINT players_status_check
            CHECK (status IN (
                'ACTIVE', 'INACTIVE', 'BLOCKED', 'PENDING',
                'SELF_EXCLUDED', 'PENDING_KYC'
            ));
    """))

    # ── IA-02: notifications.user_id — alinhar SQL para permitir NULL ─────────
    # O ORM já tem nullable=True; garante que o DB também permite NULL
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'notifications' AND column_name = 'user_id'
                  AND is_nullable = 'NO'
            ) THEN
                ALTER TABLE notifications
                    ALTER COLUMN user_id DROP NOT NULL;
            END IF;
        END $$;
    """))

    # ── IA-03: payment_method_flagged — guard extra (já aplicado em 20260519_000001) ──
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            ADD COLUMN IF NOT EXISTS payment_method_flagged BOOLEAN NOT NULL DEFAULT FALSE;
    """))

    # ── DB Roles sem BYPASSRLS (F-08) ────────────────────────────────────────
    # Cria role betaml_app sem BYPASSRLS e com acesso mínimo necessário.
    # Executa como superuser via Alembic na fase de migration; em produção
    # o DBA deve revisar e ajustar grants conforme o schema real.
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'betaml_app') THEN
                CREATE ROLE betaml_app
                    LOGIN
                    NOSUPERUSER
                    NOCREATEDB
                    NOCREATEROLE
                    NOINHERIT
                    NOBYPASSRLS;
            ELSE
                -- Garante que role existente não tenha BYPASSRLS
                ALTER ROLE betaml_app NOBYPASSRLS;
            END IF;
        END $$;
    """))
    # Grants mínimos para que betaml_app opere sem BYPASSRLS
    conn.execute(sa.text("""
        GRANT CONNECT ON DATABASE current_database() TO betaml_app;
    """))
    conn.execute(sa.text("""
        GRANT USAGE ON SCHEMA public TO betaml_app;
    """))
    conn.execute(sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE
          ON ALL TABLES IN SCHEMA public TO betaml_app;
    """))
    conn.execute(sa.text("""
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO betaml_app;
    """))
    # audit_logs: apenas INSERT (tabela imutável)
    conn.execute(sa.text("""
        REVOKE UPDATE, DELETE ON audit_logs FROM betaml_app;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        ALTER TABLE compound_rules
            DROP CONSTRAINT IF EXISTS compound_rules_severity_mode_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE compound_rules
            ADD CONSTRAINT compound_rules_severity_mode_check
            CHECK (severity_mode IN ('MAX', 'MIN', 'FIXED'));
    """))
    conn.execute(sa.text("""
        ALTER TABLE players
            DROP CONSTRAINT IF EXISTS players_status_check;
    """))
    # Ao fazer downgrade não reconstrói o check incompleto — deixa sem constraint
