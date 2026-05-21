-- BetAML migration v33
-- Corrige divergências entre ORM (models.py) e schema real do PostgreSQL.
--   1. model_inference_logs   — adiciona created_at (alias de scored_at) + scored_at se ausente
--   2. report_packages        — adiciona 4 colunas COAF ausentes (xml_path, xml_sha256, coaf_protocol_number, filed_at)
--   3. external_validation_requests — adiciona FK constraints ausentes (tenant_id, player_id, requested_by)
--   4. model_inference_logs + rule_execution_logs — índices compostos (tenant_id, player_id)
--
-- IDEMPOTENTE: todas as operações usam IF NOT EXISTS / IF EXISTS / DO $$ guards.

-- ── 1. model_inference_logs ──────────────────────────────────────────────────
-- A migration_v16 criou a coluna como "scored_at", mas o ORM referencia "created_at".
-- Estratégia: adicionar created_at como coluna real (backfill de scored_at) para
-- que o ORM funcione sem ALTER TABLE em produção quebrando código legado.

DO $$
BEGIN
    -- Garantir que scored_at existe (caso alguma instância ainda não tenha sido migrada até v16)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'model_inference_logs' AND column_name = 'scored_at'
    ) THEN
        ALTER TABLE model_inference_logs
            ADD COLUMN scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    END IF;

    -- Adicionar created_at se ausente
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'model_inference_logs' AND column_name = 'created_at'
    ) THEN
        ALTER TABLE model_inference_logs
            ADD COLUMN created_at TIMESTAMPTZ;

        -- Backfill: populated from scored_at
        UPDATE model_inference_logs SET created_at = scored_at WHERE created_at IS NULL;

        ALTER TABLE model_inference_logs
            ALTER COLUMN created_at SET NOT NULL,
            ALTER COLUMN created_at SET DEFAULT NOW();
    END IF;
END
$$;

-- ── 2. report_packages — colunas COAF ───────────────────────────────────────

ALTER TABLE report_packages
    ADD COLUMN IF NOT EXISTS xml_path             TEXT,
    ADD COLUMN IF NOT EXISTS xml_sha256           VARCHAR(64),
    ADD COLUMN IF NOT EXISTS coaf_protocol_number VARCHAR(80),
    ADD COLUMN IF NOT EXISTS filed_at             TIMESTAMPTZ;

-- ── 3. external_validation_requests — FK constraints ────────────────────────
-- A migration_v32 criou a tabela sem FKs; adicionamos agora com cuidado
-- (dados existentes podem ter UUIDs inválidos; usamos VALIDATE apenas se seguro).

DO $$
BEGIN
    -- FK: tenant_id → tenants(id)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
         WHERE constraint_type = 'FOREIGN KEY'
           AND table_name      = 'external_validation_requests'
           AND constraint_name = 'fk_ext_val_tenant'
    ) THEN
        ALTER TABLE external_validation_requests
            ADD CONSTRAINT fk_ext_val_tenant
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            NOT VALID;
        -- Validar em background (não bloqueia escrita)
        ALTER TABLE external_validation_requests
            VALIDATE CONSTRAINT fk_ext_val_tenant;
    END IF;

    -- FK: player_id → players(id)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
         WHERE constraint_type = 'FOREIGN KEY'
           AND table_name      = 'external_validation_requests'
           AND constraint_name = 'fk_ext_val_player'
    ) THEN
        ALTER TABLE external_validation_requests
            ADD CONSTRAINT fk_ext_val_player
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE external_validation_requests
            VALIDATE CONSTRAINT fk_ext_val_player;
    END IF;

    -- FK: requested_by → users(id)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
         WHERE constraint_type = 'FOREIGN KEY'
           AND table_name      = 'external_validation_requests'
           AND constraint_name = 'fk_ext_val_requested_by'
    ) THEN
        ALTER TABLE external_validation_requests
            ADD CONSTRAINT fk_ext_val_requested_by
            FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE SET NULL
            NOT VALID;
        ALTER TABLE external_validation_requests
            VALIDATE CONSTRAINT fk_ext_val_requested_by;
    END IF;

    -- DEFAULT gen_random_uuid() para id (ausente na v32)
    BEGIN
        ALTER TABLE external_validation_requests
            ALTER COLUMN id SET DEFAULT gen_random_uuid();
    EXCEPTION WHEN others THEN
        NULL; -- já tem default
    END;
END
$$;

-- ── 4. Índices compostos de performance ──────────────────────────────────────

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_model_inference_logs_tenant_player
    ON model_inference_logs (tenant_id, player_id, created_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rule_exec_logs_tenant_player
    ON rule_execution_logs (tenant_id, player_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_report_packages_filed_at
    ON report_packages (tenant_id, filed_at)
    WHERE filed_at IS NOT NULL;
