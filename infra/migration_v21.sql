-- migration_v21.sql — Adiciona cpf_hmac para lookup indexado O(1) em players
-- Conformidade: LGPD Art. 46 (proteção por design) — HMAC não permite reversão do CPF
-- Performance: substitui scan O(n) com decrypt por lookup indexado O(1) via HMAC-SHA256
-- Referência: Portaria SPA/MF 1.143/2024, COAF Res. 36/2021

BEGIN;

-- 1. Adicionar coluna cpf_hmac (nullable para compatibilidade retroativa)
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS cpf_hmac VARCHAR(64);

-- 2. Criar índice B-tree para lookup O(1) por tenant+cpf_hmac
--    (não único pois CPFs distintos podem existir em tenants diferentes)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_players_tenant_cpf_hmac
    ON players (tenant_id, cpf_hmac)
    WHERE cpf_hmac IS NOT NULL;

-- NOTA: O backfill dos valores cpf_hmac para players existentes deve ser executado
-- via script Python (scripts/backfill_cpf_hmac.py) que tem acesso à PII_ENCRYPTION_KEY.
-- Não é possível computar HMAC-SHA256 com key secreta diretamente em SQL.
-- Execute APÓS aplicar esta migration:
--   python scripts/backfill_cpf_hmac.py --batch-size=500

COMMIT;
