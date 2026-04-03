-- ============================================================
-- BetAML — Migration v29
-- Multi-modalidade: suporte a casino, slots, jogos online
-- Lei 14.790/2023 art. 3º, I e II — apostas de quota fixa em:
--   (I) eventos reais de temática esportiva
--   (II) eventos virtuais de jogos on-line
-- Portaria SPA/MF 1.143/2024 — regulamentação dos jogos online
-- ============================================================

BEGIN;

-- ──────────────────────────────────────────────────
-- 1. Novas colunas na tabela bets para modalidades não-esportivas
-- ──────────────────────────────────────────────────
ALTER TABLE bets
    ADD COLUMN IF NOT EXISTS product_type  TEXT NOT NULL DEFAULT 'SPORTSBOOK',
    ADD COLUMN IF NOT EXISTS game_id       TEXT,
    ADD COLUMN IF NOT EXISTS game_name     TEXT,
    ADD COLUMN IF NOT EXISTS game_provider TEXT,
    ADD COLUMN IF NOT EXISTS game_category TEXT,
    ADD COLUMN IF NOT EXISTS rtp_teorico   DECIMAL(6,4);

-- ──────────────────────────────────────────────────
-- 2. Backfill: mapear bet_type legado → product_type canônico
-- ──────────────────────────────────────────────────
UPDATE bets SET product_type = CASE
    WHEN bet_type = 'SPORTS'      THEN 'SPORTSBOOK'
    WHEN bet_type = 'CASINO_LIVE' THEN 'CASINO_LIVE'
    WHEN bet_type = 'SLOTS'       THEN 'SLOT'
    WHEN bet_type = 'VIRTUAL'     THEN 'VIRTUAL'
    ELSE 'SPORTSBOOK'
END
WHERE product_type = 'SPORTSBOOK' AND bet_type != 'SPORTS';

-- ──────────────────────────────────────────────────
-- 3. Índices para queries por product_type
-- ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_bets_product_type
    ON bets (product_type);

CREATE INDEX IF NOT EXISTS idx_bets_tenant_product
    ON bets (tenant_id, product_type);

COMMIT;
