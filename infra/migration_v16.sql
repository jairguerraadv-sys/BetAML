-- Migration v16: A/B testing traffic split + inference logs
-- Adds:
--   - scoring_configs.ml_challenger_pct (0..100)
--   - model_inference_logs table for analytics

ALTER TABLE scoring_configs
    ADD COLUMN IF NOT EXISTS ml_challenger_pct INTEGER NOT NULL DEFAULT 0;

-- Inference logs (for A/B analytics)
CREATE TABLE IF NOT EXISTS model_inference_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id        UUID,
    model_id         UUID REFERENCES model_registry(id) ON DELETE SET NULL,
    model_variant    TEXT NOT NULL, -- champion | challenger
    anomaly_score    NUMERIC(5,4) NOT NULL,
    is_anomaly       BOOLEAN NOT NULL DEFAULT FALSE,
    request_id       TEXT,
    scored_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_inference_tenant_time
    ON model_inference_logs(tenant_id, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_inference_model
    ON model_inference_logs(tenant_id, model_id, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_model_inference_variant
    ON model_inference_logs(tenant_id, model_variant, scored_at DESC);

-- RLS
ALTER TABLE model_inference_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_inference_logs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_model_inference_logs ON model_inference_logs;
CREATE POLICY tenant_isolation_model_inference_logs
    ON model_inference_logs USING (tenant_id = current_tenant_id());
