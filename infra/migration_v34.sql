-- migration_v34.sql
-- Fase 2 – T08: adiciona triage_note aos alertas e prepara campo ML API key
-- Aplicar após migration_v33.sql

BEGIN;

-- T08: coluna de nota de triagem (era descartada silenciosamente antes desta migração)
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS triage_note TEXT;

-- Índice parcial para consultas de alertas triados com nota
CREATE INDEX IF NOT EXISTS idx_alerts_triage_note_not_null
    ON alerts (triaged_at DESC)
    WHERE triage_note IS NOT NULL;

-- T10: registrar no catálogo que ml_service requer ML_INTERNAL_API_KEY
-- (sem DDL — apenas documentação via COMMENT)
COMMENT ON TABLE model_registry IS
    'Registro de modelos ML. O ml_service exige ML_INTERNAL_API_KEY via header X-Internal-Api-Key (T10, Fase 2).';

-- Índice de suporte para busca de alertas por triagem + status (T14)
CREATE INDEX IF NOT EXISTS idx_alerts_triaged_by_status
    ON alerts (tenant_id, triaged_by, status)
    WHERE triaged_by IS NOT NULL;

COMMIT;
