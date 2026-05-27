-- ============================================================
-- BetAML — Migration v5
-- Objetivo:
--   1. Completar schema (compound_rules, model_registry, player_list_entries,
--      players.risk_band, cases.auto_created, scoring_configs thresholds)
--   2. Tornar RLS realmente efetivo (FORCE + betaml_app user não-owner)
--   3. Índices de suporte às novas queries
-- ============================================================

-- ── 1. Completar compound_rules (garante colunas usadas pelo rules_engine) ──
ALTER TABLE compound_rules
    ADD COLUMN IF NOT EXISTS logic               TEXT,
    ADD COLUMN IF NOT EXISTS component_rule_ids  JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS score_weights       JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS min_score_threshold DECIMAL(5,4),
    ADD COLUMN IF NOT EXISTS is_active           BOOLEAN NOT NULL DEFAULT TRUE;

-- Sincronizar 'logic' com 'operator' para registros existentes
UPDATE compound_rules SET logic = operator WHERE logic IS NULL;

-- ── 2. Completar player_list_entries (coluna 'value' usada pelo rules_engine) ─
ALTER TABLE player_list_entries
    ADD COLUMN IF NOT EXISTS value      TEXT,
    ADD COLUMN IF NOT EXISTS value_type TEXT,
    ADD COLUMN IF NOT EXISTS player_list_id UUID REFERENCES player_lists(id) ON DELETE CASCADE;

-- Migrate: preencher value com external_player_id quando value for NULL
UPDATE player_list_entries
SET value      = COALESCE(external_player_id, cpf_hash),
    value_type = CASE
                    WHEN cpf_hash IS NOT NULL THEN 'CPF_HASH'
                    WHEN external_player_id IS NOT NULL THEN 'EXTERNAL_ID'
                    ELSE 'UNKNOWN'
                 END
WHERE value IS NULL AND (external_player_id IS NOT NULL OR cpf_hash IS NOT NULL);

-- ── 3. Completar model_registry (alinhar com ml_service) ────────────────────
-- Colunas adicionadas em migration_v4; garantir presença:
ALTER TABLE model_registry
    ADD COLUMN IF NOT EXISTS artifact_uri    TEXT,
    ADD COLUMN IF NOT EXISTS is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS training_rows   INT,
    ADD COLUMN IF NOT EXISTS feature_columns JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS trained_by      TEXT,
    ADD COLUMN IF NOT EXISTS model_name      TEXT;

-- Backfill: artifact_uri <-> artifact_path devem ter o mesmo valor
UPDATE model_registry SET artifact_uri  = artifact_path WHERE artifact_uri  IS NULL AND artifact_path IS NOT NULL;
UPDATE model_registry SET artifact_path = artifact_uri  WHERE artifact_path IS NULL AND artifact_uri  IS NOT NULL;
-- Sincronizar is_active <-> active
UPDATE model_registry SET is_active = active WHERE is_active IS DISTINCT FROM active;
-- Backfill model_name com algorithm quando ausente
UPDATE model_registry SET model_name = algorithm WHERE model_name IS NULL;

-- ── 4. players: adicionar risk_band ─────────────────────────────────────────
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS risk_band TEXT NOT NULL DEFAULT 'LOW'
        CHECK (risk_band IN ('LOW', 'MEDIUM', 'HIGH'));

-- Calcular risk_band inicial a partir do risk_score existente
UPDATE players SET risk_band = CASE
    WHEN risk_score >= 0.70 THEN 'HIGH'
    WHEN risk_score >= 0.35 THEN 'MEDIUM'
    ELSE 'LOW'
END;

-- ── 5. cases: auto_created + source_alert_id ────────────────────────────────
ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS auto_created        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS auto_created_reason TEXT,
    ADD COLUMN IF NOT EXISTS source_alert_id     UUID REFERENCES alerts(id);

-- ── 6. scoring_configs: thresholds de banda de risco e compatibilidade renda ─
ALTER TABLE scoring_configs
    ADD COLUMN IF NOT EXISTS risk_band_low_threshold        DECIMAL(5,4) NOT NULL DEFAULT 0.35,
    ADD COLUMN IF NOT EXISTS risk_band_high_threshold       DECIMAL(5,4) NOT NULL DEFAULT 0.70,
    ADD COLUMN IF NOT EXISTS income_volume_ratio_threshold  DECIMAL(5,2) NOT NULL DEFAULT 1.50;

-- ── 7. RLS — FORCE ROW LEVEL SECURITY (impede bypass pelo owner betaml) ──────
-- Antes do FORCE, o user 'betaml' (que é owner) bypassava as policies.
-- Agora o FORCE garante que mesmo o owner deva obedecer as policies.
-- IMPORTANT: seeds e migrations devem rodar como superuser (postgres),
-- que ainda bypassa RLS mesmo com FORCE.

ALTER TABLE players         FORCE ROW LEVEL SECURITY;
ALTER TABLE alerts          FORCE ROW LEVEL SECURITY;
ALTER TABLE cases           FORCE ROW LEVEL SECURITY;
ALTER TABLE case_events     FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_logs      FORCE ROW LEVEL SECURITY;
ALTER TABLE report_packages FORCE ROW LEVEL SECURITY;
ALTER TABLE rule_definitions FORCE ROW LEVEL SECURITY;
ALTER TABLE compound_rules  FORCE ROW LEVEL SECURITY;
ALTER TABLE mapping_configs FORCE ROW LEVEL SECURITY;
ALTER TABLE ingest_jobs     FORCE ROW LEVEL SECURITY;
ALTER TABLE player_lists    FORCE ROW LEVEL SECURITY;
ALTER TABLE player_list_entries FORCE ROW LEVEL SECURITY;
ALTER TABLE feature_snapshots   FORCE ROW LEVEL SECURITY;
ALTER TABLE scoring_configs     FORCE ROW LEVEL SECURITY;
ALTER TABLE notifications       FORCE ROW LEVEL SECURITY;

-- RLS policies para tabelas novas (compound_rules, player_lists, etc.)
ALTER TABLE compound_rules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS compound_rules_tenant_isolation ON compound_rules;
CREATE POLICY compound_rules_tenant_isolation ON compound_rules
    USING (tenant_id = current_tenant_id());

ALTER TABLE player_lists ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS player_lists_tenant_isolation ON player_lists;
CREATE POLICY player_lists_tenant_isolation ON player_lists
    USING (tenant_id = current_tenant_id());

ALTER TABLE player_list_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS player_list_entries_tenant_isolation ON player_list_entries;
CREATE POLICY player_list_entries_tenant_isolation ON player_list_entries
    USING (tenant_id = current_tenant_id());

ALTER TABLE feature_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS feature_snapshots_tenant_isolation ON feature_snapshots;
CREATE POLICY feature_snapshots_tenant_isolation ON feature_snapshots
    USING (tenant_id = current_tenant_id());

ALTER TABLE scoring_configs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scoring_configs_tenant_isolation ON scoring_configs;
CREATE POLICY scoring_configs_tenant_isolation ON scoring_configs
    USING (tenant_id = current_tenant_id());

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS notifications_tenant_isolation ON notifications;
CREATE POLICY notifications_tenant_isolation ON notifications
    USING (tenant_id = current_tenant_id());

-- ── 8. Criar betaml_app (usuário da aplicação, não-owner, obedece RLS) ───────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'betaml_app') THEN
        CREATE ROLE betaml_app WITH LOGIN PASSWORD 'app-set-via-secret-manager';
    END IF;
END;
$$;

-- Conceder uso do schema e SELECT/INSERT/UPDATE/DELETE nas tabelas de negócio
GRANT CONNECT ON DATABASE betaml_dev TO betaml_app;
GRANT USAGE ON SCHEMA public TO betaml_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO betaml_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO betaml_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO betaml_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO betaml_app;

-- betaml_app NÃO é owner e NÃO possui BYPASSRLS → obedece FORCE RLS
-- betaml (owner original) também obedece FORCE RLS agora
-- superuser 'postgres' ainda bypassa (para migrations/seeds)

-- ── 9. Índices para novas queries ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_players_risk_band       ON players(tenant_id, risk_band);
CREATE INDEX IF NOT EXISTS idx_players_risk_score      ON players(tenant_id, risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_cases_auto_created      ON cases(tenant_id, auto_created);
CREATE INDEX IF NOT EXISTS idx_cases_sla_due           ON cases(tenant_id, sla_due_at) WHERE status NOT IN ('CLOSED','REPORTED','ARCHIVED');
CREATE INDEX IF NOT EXISTS idx_compound_rules_active   ON compound_rules(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_player_list_entries_val ON player_list_entries(tenant_id, value);
CREATE INDEX IF NOT EXISTS idx_model_registry_active   ON model_registry(tenant_id, is_active, algorithm);

-- ── Fim da migration v5 ───────────────────────────────────────────────────────
