-- ============================================================
-- BetAML — Migration v28
-- Adiciona colunas features, cluster_id, cluster_size em players
-- Necessárias para ML Trainer (network_clustering, recurrence_estimator)
-- Ref: Lei 14.790/2023 | Portaria SPA/MF 1.143/2024
-- ============================================================

-- ──────────────────────────────────────────────────
-- 1. features JSONB — armazena features ML do player (scores, embeddings, flags)
-- ──────────────────────────────────────────────────
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS features JSONB NOT NULL DEFAULT '{}';

-- ──────────────────────────────────────────────────
-- 2. cluster_id — identificador do cluster de rede (DBSCAN network_clustering)
-- ──────────────────────────────────────────────────
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS cluster_id INTEGER;

-- ──────────────────────────────────────────────────
-- 3. cluster_size — tamanho do cluster (contagem de players no mesmo grupo)
-- ──────────────────────────────────────────────────
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS cluster_size INTEGER NOT NULL DEFAULT 0;

-- Índice para busca rápida por cluster (ex.: investigação de redes)
CREATE INDEX IF NOT EXISTS idx_players_cluster_id
    ON players (tenant_id, cluster_id)
    WHERE cluster_id IS NOT NULL;

COMMENT ON COLUMN players.features IS
    'JSONB com features ML calculadas pelo ML Trainer (anomaly_score, recurrence_score, network features etc.)';
COMMENT ON COLUMN players.cluster_id IS
    'ID do cluster DBSCAN detectado pelo network_clustering. NULL = sem cluster.';
COMMENT ON COLUMN players.cluster_size IS
    'Tamanho do cluster de rede ao qual o player pertence. 0 = isolado.';
