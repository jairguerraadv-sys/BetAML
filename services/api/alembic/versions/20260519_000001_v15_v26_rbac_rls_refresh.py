"""Gap coverage v15–v26: refresh token, A/B split, RLS hardening, LGPD/SIGAP,
multi-role RBAC, plan_tier, backfill tracking prep.

Revision ID: 20260519_000001
Revises: 20260402_000001
Create Date: 2026-05-19 00:00:01

Porta para Alembic os DDLs dos arquivos migration_v15.sql até migration_v26.sql
que ainda não tinham cobertura na trilha Alembic.  Todas as operações são
idempotentes (IF NOT EXISTS / IF EXISTS / DO-block com verificação de
information_schema) para segurança em bases criadas pelos dois caminhos
(docker-compose SQL ou alembic upgrade head).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260519_000001"
down_revision = "20260402_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── v15: refresh_token_jti em users ──────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS refresh_token_jti TEXT;
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_users_refresh_token_jti
            ON users(refresh_token_jti);
    """))

    # ── v16: ml_challenger_pct em scoring_configs ─────────────────────────────
    # (a tabela model_inference_logs é criada pela Alembic v14; scored_at→created_at
    #  será tratado na migration 20260519_000003_integrity_fixes)
    conn.execute(sa.text("""
        ALTER TABLE scoring_configs
            ADD COLUMN IF NOT EXISTS ml_challenger_pct INTEGER NOT NULL DEFAULT 0;
    """))

    # ── v17: remove constraint legada players_status_check ───────────────────
    conn.execute(sa.text("""
        ALTER TABLE players
            DROP CONSTRAINT IF EXISTS players_status_check;
    """))

    # ── v18: RLS em api_keys, ingest_errors, external_validation_requests ────
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
        $$ LANGUAGE SQL STABLE;
    """))
    for tbl, policy in [
        ("api_keys",                       "tenant_isolation_api_keys"),
        ("ingest_errors",                  "tenant_isolation_ingest_errors"),
        ("external_validation_requests",   "tenant_isolation_external_validation_requests"),
    ]:
        conn.execute(sa.text(f"""
            DO $$
            BEGIN
                IF to_regclass('public.{tbl}') IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY';
                    EXECUTE 'ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY';
                    EXECUTE 'DROP POLICY IF EXISTS {policy} ON {tbl}';
                    EXECUTE 'CREATE POLICY {policy} ON {tbl}
                             USING (tenant_id = current_tenant_id())';
                END IF;
            END
            $$;
        """))

    # Trigger de imutabilidade em audit_logs
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is immutable: operation % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """))
    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_prevent_audit_log_update_delete ON audit_logs;
    """))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_prevent_audit_log_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
    """))

    # ── v19: normalizar workflow enterprise de cases ──────────────────────────
    conn.execute(sa.text("""
        UPDATE cases
        SET status = CASE status
            WHEN 'IN_REVIEW'    THEN 'INVESTIGATING'
            WHEN 'PENDING_INFO' THEN 'PENDING_REVIEW'
            WHEN 'CLOSED_SAR'   THEN 'REPORTED'
            WHEN 'CLOSED_SAT'   THEN 'CLOSED'
            ELSE status
        END
        WHERE status IN ('IN_REVIEW', 'PENDING_INFO', 'CLOSED_SAR', 'CLOSED_SAT');
    """))
    conn.execute(sa.text("""
        ALTER TABLE cases
            DROP CONSTRAINT IF EXISTS cases_status_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE cases
            ADD CONSTRAINT cases_status_check
            CHECK (status IN ('OPEN', 'INVESTIGATING', 'PENDING_REVIEW', 'CLOSED', 'REPORTED'));
    """))

    # ── v20: status PARTIAL em ingest_jobs ───────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE ingest_jobs
            DROP CONSTRAINT IF EXISTS ingest_jobs_status_check;
    """))
    conn.execute(sa.text("""
        ALTER TABLE ingest_jobs
            ADD CONSTRAINT ingest_jobs_status_check
            CHECK (status IN ('QUEUED', 'PROCESSING', 'DONE', 'FAILED', 'PARTIAL'));
    """))

    # ── v21: cpf_hmac para lookup indexado O(1) ───────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE players
            ADD COLUMN IF NOT EXISTS cpf_hmac VARCHAR(64);
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_players_tenant_cpf_hmac
            ON players (tenant_id, cpf_hmac)
            WHERE cpf_hmac IS NOT NULL;
    """))

    # ── v22: consolidação de campos canônicos ────────────────────────────────

    # CompoundRule: sincronizar e remover aliases
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'compound_rules' AND column_name = 'operator'
            ) THEN
                UPDATE compound_rules
                   SET logic = operator
                 WHERE logic IS NULL AND operator IS NOT NULL;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'compound_rules' AND column_name = 'child_rule_ids'
            ) THEN
                UPDATE compound_rules
                   SET component_rule_ids = child_rule_ids
                 WHERE (component_rule_ids IS NULL OR component_rule_ids = '[]'::jsonb)
                   AND child_rule_ids IS NOT NULL
                   AND child_rule_ids != '[]'::jsonb;
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        UPDATE compound_rules SET logic = 'AND' WHERE logic IS NULL;
    """))
    conn.execute(sa.text("""
        ALTER TABLE compound_rules
            DROP COLUMN IF EXISTS operator,
            DROP COLUMN IF EXISTS child_rule_ids;
    """))

    # ModelRegistry: sincronizar e remover aliases
    for old_col, new_col in [
        ("active",        "is_active"),
        ("artifact_path", "artifact_uri"),
        ("sample_count",  "training_rows"),
    ]:
        conn.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'model_registry' AND column_name = '{old_col}'
                ) THEN
                    UPDATE model_registry
                       SET {new_col} = {old_col}
                     WHERE {new_col} IS DISTINCT FROM {old_col}
                       AND {old_col} IS NOT NULL;
                    ALTER TABLE model_registry DROP COLUMN IF EXISTS {old_col};
                END IF;
            END $$;
        """))

    # Player: remover full_name (PII em claro)
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'players' AND column_name = 'full_name'
            ) THEN
                UPDATE players
                   SET name_encrypted = 'PENDING_MIGRATION'::bytea
                 WHERE (name_encrypted IS NULL OR length(name_encrypted) = 0)
                   AND full_name IS NOT NULL AND full_name <> '';
                ALTER TABLE players DROP COLUMN IF EXISTS full_name;
            END IF;
        END $$;
    """))

    # PlayerListEntry: índice de unicidade (list_id, value)
    conn.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_list_entries_unique_val
            ON player_list_entries (list_id, value)
            WHERE value IS NOT NULL;
    """))

    # ── v23: índices de rede e observabilidade ────────────────────────────────
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_device_events_device_hash ON device_events (tenant_id, device_hash, player_id) WHERE device_hash IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_device_events_ip_hash ON device_events (tenant_id, ip_hash, player_id) WHERE ip_hash IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_financial_transactions_bank_account_hash ON financial_transactions (tenant_id, bank_account_hash, player_id) WHERE bank_account_hash IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_financial_transactions_payment_instrument ON financial_transactions (tenant_id, payment_instrument, player_id) WHERE payment_instrument IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_feature_snapshots_tenant_created ON feature_snapshots (tenant_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ingest_errors_tenant_created ON ingest_errors (tenant_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_report_packages_tenant_status_created ON report_packages (tenant_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_tenant_label_created ON alerts (tenant_id, label, created_at) WHERE label IS NOT NULL",
    ]:
        conn.execute(sa.text(stmt + ";"))

    # ── v24: compliance Lei 14.790/2023 ──────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            DROP CONSTRAINT IF EXISTS chk_transaction_type;
    """))
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            ADD CONSTRAINT chk_transaction_type
            CHECK (type IN (
                'DEPOSIT','WITHDRAWAL','REVERSAL',
                'BONUS','FREE_BET','CASHOUT','ADJUSTMENT','CHARGEBACK'
            ));
    """))
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            DROP CONSTRAINT IF EXISTS chk_payment_method;
    """))
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            ADD CONSTRAINT chk_payment_method
            CHECK (payment_method IN (
                'PIX','TED','DEBIT','CARD_DEBIT','CARD_CREDIT','WALLET','OTHER','CARD'
            ));
    """))
    conn.execute(sa.text("""
        ALTER TABLE financial_transactions
            ADD COLUMN IF NOT EXISTS payment_method_flagged BOOLEAN NOT NULL DEFAULT FALSE;
    """))
    conn.execute(sa.text("""
        UPDATE financial_transactions
            SET payment_method_flagged = TRUE
            WHERE payment_method = 'CARD_CREDIT';
    """))
    conn.execute(sa.text("""
        ALTER TABLE players
            ADD COLUMN IF NOT EXISTS self_exclusion_flag  BOOLEAN      NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS deposit_limit_daily  NUMERIC(15,2);
    """))
    # Renomear multi_currency_flag → inconsistent_currency_flag se existir
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'feature_snapshots' AND column_name = 'multi_currency_flag'
            ) THEN
                ALTER TABLE feature_snapshots
                    RENAME COLUMN multi_currency_flag TO inconsistent_currency_flag;
            END IF;
        END $$;
    """))

    # ── v25: RBAC multi-role por usuário ──────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE users ALTER COLUMN role TYPE varchar(50);
    """))
    conn.execute(sa.text("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS roles JSONB DEFAULT '[]'::jsonb;
    """))
    conn.execute(sa.text("""
        UPDATE users SET roles =
          CASE role
            WHEN 'AML_ANALYST' THEN '["Operador_Analista"]'::jsonb
            WHEN 'AUDITOR'     THEN '["Operador_Analista"]'::jsonb
            WHEN 'ADMIN'       THEN '["Operador_Gestor","Operador_AdminTecnico","Operador_Analista"]'::jsonb
            WHEN 'SUPER_ADMIN' THEN '["BetAML_SuperAdmin"]'::jsonb
            ELSE '[]'::jsonb
          END
        WHERE roles = '[]'::jsonb OR roles IS NULL;
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_users_roles ON users USING GIN (roles);
    """))

    # ── v26: plan_tier + RLS em tenants ──────────────────────────────────────
    conn.execute(sa.text("""
        ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS plan_tier VARCHAR(20) NOT NULL DEFAULT 'standard';
    """))
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'tenants' AND constraint_name = 'tenants_plan_tier_check'
            ) THEN
                ALTER TABLE tenants
                    ADD CONSTRAINT tenants_plan_tier_check
                    CHECK (plan_tier IN ('starter', 'standard', 'professional', 'enterprise'));
            END IF;
        END $$;
    """))
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
        $$ LANGUAGE SQL STABLE;
    """))
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF to_regclass('public.tenants') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE tenants ENABLE ROW LEVEL SECURITY';
                EXECUTE 'ALTER TABLE tenants FORCE ROW LEVEL SECURITY';
                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_select ON tenants';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_tenants_select ON tenants
                        FOR SELECT
                        USING (id = current_tenant_id() OR current_tenant_id() IS NULL)
                $pol$;
                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_insert ON tenants';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_tenants_insert ON tenants
                        FOR INSERT
                        WITH CHECK (id = current_tenant_id() OR current_tenant_id() IS NULL)
                $pol$;
                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_update ON tenants';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_tenants_update ON tenants
                        FOR UPDATE
                        USING (id = current_tenant_id())
                $pol$;
                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_tenants_delete ON tenants';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_tenants_delete ON tenants
                        FOR DELETE
                        USING (id = current_tenant_id())
                $pol$;
            END IF;
        END $$;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE tenants DROP COLUMN IF EXISTS plan_tier"))
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS roles"))
    conn.execute(sa.text("ALTER TABLE players DROP COLUMN IF EXISTS self_exclusion_flag, DROP COLUMN IF EXISTS deposit_limit_daily"))
    conn.execute(sa.text("ALTER TABLE financial_transactions DROP COLUMN IF EXISTS payment_method_flagged"))
    conn.execute(sa.text("ALTER TABLE players DROP COLUMN IF EXISTS cpf_hmac"))
    conn.execute(sa.text("ALTER TABLE scoring_configs DROP COLUMN IF EXISTS ml_challenger_pct"))
    conn.execute(sa.text("ALTER TABLE users DROP COLUMN IF EXISTS refresh_token_jti"))
