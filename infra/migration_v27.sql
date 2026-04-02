-- ============================================================
-- BetAML — Migration v27
-- Gaps do Motor de Histórias: ingest_mode + backfill tracking
-- Ref: Lei 14.790/2023 | Portaria SPA/MF 1.143/2024 | 1.231/2024
-- ============================================================

-- ──────────────────────────────────────────────────
-- GAP-E2: ingest_jobs — modo de ingestão e flag backfill
-- ──────────────────────────────────────────────────
ALTER TABLE ingest_jobs
    ADD COLUMN IF NOT EXISTS ingest_mode  VARCHAR(20) NOT NULL DEFAULT 'incremental',
    ADD COLUMN IF NOT EXISTS is_backfill  BOOLEAN     NOT NULL DEFAULT FALSE;

-- Backfill retroativo: jobs de reprocessamento existentes marcados como 'reprocess'
UPDATE ingest_jobs
SET ingest_mode = 'reprocess'
WHERE reprocessed_from IS NOT NULL
  AND ingest_mode = 'incremental';

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_ingest_mode
    ON ingest_jobs (tenant_id, ingest_mode, created_at DESC);

-- ──────────────────────────────────────────────────
-- GAP-R3: alerts — rastreabilidade de proveniência para
-- alertas gerados via backfill / reprocessamento
-- ──────────────────────────────────────────────────
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS ingest_mode     VARCHAR(20) NOT NULL DEFAULT 'incremental',
    ADD COLUMN IF NOT EXISTS backfill_job_id TEXT;

CREATE INDEX IF NOT EXISTS idx_alerts_ingest_mode
    ON alerts (tenant_id, ingest_mode, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_backfill_job
    ON alerts (tenant_id, backfill_job_id)
    WHERE backfill_job_id IS NOT NULL;

-- Constraint: garante que backfill_job_id só seja preenchido para modos não-incrementais
-- (informativa — NÃO impede operação em caso de inconsistência)
COMMENT ON COLUMN alerts.backfill_job_id IS
    'ID do IngestJob de backfill/reprocess que originou este alerta. '
    'Preenchido quando ingest_mode IN (''backfill'', ''reprocess'').';

-- ──────────────────────────────────────────────────
-- GAP-C2: cases — proveniência de backfill
-- Permite identificar casos abertos a partir de análise retroativa
-- e evitar duplicatas em reprocessamentos futuros
-- ──────────────────────────────────────────────────
ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS backfill_job_id TEXT,
    ADD COLUMN IF NOT EXISTS ingest_mode     VARCHAR(20) NOT NULL DEFAULT 'incremental';

CREATE INDEX IF NOT EXISTS idx_cases_backfill_job
    ON cases (tenant_id, backfill_job_id)
    WHERE backfill_job_id IS NOT NULL;

COMMENT ON COLUMN cases.backfill_job_id IS
    'ID do IngestJob de backfill/reprocess que originou este caso automaticamente.';

-- ──────────────────────────────────────────────────
-- GAP-C2: deduplicação de casos em reprocessamento
-- Índice auxiliar para a query de "verificar caso existente no período"
-- usada pelo rules_engine ao decidir criar novo caso vs. enriquecer existente
-- ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_cases_open_player
    ON cases (tenant_id, player_id, status, created_at DESC)
    WHERE status IN ('OPEN', 'IN_REVIEW', 'INVESTIGATING', 'PENDING_REVIEW');


-- ──────────────────────────────────────────────────
-- GAP-stream: tabela OLTP player_kyc_events
-- Persiste eventos KYC, Jogo Responsável e mudança de status de conta
-- (Portaria SPA/MF 1.143/2024 art. 9° — auto-exclusão SIGAP)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS player_kyc_events (
    id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                UUID         NOT NULL,
    player_id                TEXT         NOT NULL,
    entity_type              VARCHAR(40)  NOT NULL,  -- KYC_EVENT | RESPONSIBLE_GAMBLING_EVENT | ACCOUNT_STATUS_CHANGE
    subtype                  VARCHAR(60)  NOT NULL,
    provider                 TEXT,
    document_type            TEXT,
    pep_flag                 BOOLEAN      NOT NULL DEFAULT FALSE,
    income_declared          NUMERIC(18,2),
    -- Jogo Responsável
    exclusion_source         TEXT,
    exclusion_scope          TEXT,
    exclusion_duration_days  INTEGER,
    old_deposit_limit        NUMERIC(18,2),
    new_deposit_limit        NUMERIC(18,2),
    -- Mudança de status
    previous_status          TEXT,
    new_status               TEXT,
    reason                   TEXT,
    -- Rastreabilidade de backfill
    ingest_mode              VARCHAR(20)  NOT NULL DEFAULT 'incremental',
    backfill_job_id          TEXT,
    occurred_at              TIMESTAMPTZ  NOT NULL,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pkyc_player
    ON player_kyc_events (tenant_id, player_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_pkyc_subtype
    ON player_kyc_events (tenant_id, entity_type, subtype, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_pkyc_backfill
    ON player_kyc_events (tenant_id, backfill_job_id)
    WHERE backfill_job_id IS NOT NULL;

COMMENT ON TABLE player_kyc_events IS
    'Eventos de ciclo de vida do player: KYC, jogo responsável (auto-exclusão SIGAP/operador) e alterações de status. Portaria SPA/MF 1.143/2024.';
