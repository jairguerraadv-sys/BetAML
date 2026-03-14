-- ============================================================
-- BetAML — Migration v4: Row-Level Security real no Postgres
--          + ajustes menores de schema (report_packages, audit_logs)
-- ============================================================

-- ── 1. Garantir que a extensão pgcrypto está disponível ───────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── 2. Adicionar colunas faltantes em tables existentes ───────────────

-- report_packages: colunas adicionadas pelo ORM mas ausentes no init
ALTER TABLE report_packages
    ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','PENDING_REVIEW','FILED','REJECTED')),
    ADD COLUMN IF NOT EXISTS analyst_narrative TEXT,
    ADD COLUMN IF NOT EXISTS decision          TEXT
        CHECK (decision IN ('FILE_SAR','NO_ACTION','PENDING') OR decision IS NULL),
    ADD COLUMN IF NOT EXISTS pdf_path          TEXT;

-- cases: reference_number e sla_due_at
ALTER TABLE cases
    ADD COLUMN IF NOT EXISTS reference_number TEXT,
    ADD COLUMN IF NOT EXISTS sla_due_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS priority         TEXT NOT NULL DEFAULT 'MEDIUM'
        CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL'));

-- alerts: composite_score, score_breakdown, labels
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS composite_score  DECIMAL(5,4),
    ADD COLUMN IF NOT EXISTS score_breakdown  JSONB,
    ADD COLUMN IF NOT EXISTS rule_weight      DECIMAL(4,3) DEFAULT 0.4,
    ADD COLUMN IF NOT EXISTS ml_weight        DECIMAL(4,3) DEFAULT 0.4,
    ADD COLUMN IF NOT EXISTS network_weight   DECIMAL(4,3) DEFAULT 0.2,
    ADD COLUMN IF NOT EXISTS label            TEXT,
    ADD COLUMN IF NOT EXISTS labeled_by       UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS labeled_at       TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS compound_rule_id UUID;

-- players: external_id, full_name (plain), registered_since
ALTER TABLE players
    ADD COLUMN IF NOT EXISTS external_id        TEXT,
    ADD COLUMN IF NOT EXISTS full_name          TEXT,
    ADD COLUMN IF NOT EXISTS registered_since   DATE,
    ADD COLUMN IF NOT EXISTS status             TEXT NOT NULL DEFAULT 'ACTIVE',
    ADD COLUMN IF NOT EXISTS last_scored_at     TIMESTAMPTZ;

-- mapping_configs: versionamento
ALTER TABLE mapping_configs
    ADD COLUMN IF NOT EXISTS version_number    INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_current        BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS parent_id         UUID REFERENCES mapping_configs(id),
    ADD COLUMN IF NOT EXISTS change_notes      TEXT;

-- ingest_jobs: novos campos
ALTER TABLE ingest_jobs
    ADD COLUMN IF NOT EXISTS connector_type    TEXT NOT NULL DEFAULT 'FILE',
    ADD COLUMN IF NOT EXISTS bytes_processed   BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS duration_ms       BIGINT,
    ADD COLUMN IF NOT EXISTS error_sample      JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS reprocessed_from  UUID REFERENCES ingest_jobs(id),
    ADD COLUMN IF NOT EXISTS mapping_version_id UUID REFERENCES mapping_configs(id);

-- rule_definitions: weight e updated_by
ALTER TABLE rule_definitions
    ADD COLUMN IF NOT EXISTS weight  DECIMAL(4,3) NOT NULL DEFAULT 0.5,
    ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES users(id);

-- model_registry: campos adicionais
ALTER TABLE model_registry
    ADD COLUMN IF NOT EXISTS model_type      TEXT NOT NULL DEFAULT 'ANOMALY',
    ADD COLUMN IF NOT EXISTS artifact_uri    TEXT,
    ADD COLUMN IF NOT EXISTS is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_challenger   BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS champion_id     UUID REFERENCES model_registry(id),
    ADD COLUMN IF NOT EXISTS promoted_by     UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS promoted_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS training_rows   INT,
    ADD COLUMN IF NOT EXISTS trained_by      TEXT,
    ADD COLUMN IF NOT EXISTS feature_columns JSONB DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS sample_count    INT,
    ADD COLUMN IF NOT EXISTS dataset_window_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dataset_window_end   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status          TEXT NOT NULL DEFAULT 'STAGING';

-- ── 3. Tabelas novas (se ausentes) ───────────────────────────────────

CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    key_prefix    TEXT NOT NULL,
    source_system TEXT,
    permissions   JSONB NOT NULL DEFAULT '["ingest"]',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    created_by    UUID REFERENCES users(id),
    revoked_by    UUID REFERENCES users(id),
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rule_macros (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    expression  TEXT,        -- campo extra: alias de body_dsl
    body_dsl    TEXT,
    description TEXT,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS compound_rules (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                 TEXT NOT NULL,
    description          TEXT,
    status               TEXT NOT NULL DEFAULT 'ACTIVE',
    operator             TEXT NOT NULL DEFAULT 'AND',
    n_threshold          INT,
    child_rule_ids       JSONB NOT NULL DEFAULT '[]',
    severity_mode        TEXT NOT NULL DEFAULT 'MAX',
    fixed_severity       TEXT,
    logic                TEXT,
    component_rule_ids   JSONB DEFAULT '[]',
    score_weights        JSONB DEFAULT '{}',
    min_score_threshold  DECIMAL(5,4),
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    version              INT NOT NULL DEFAULT 1,
    created_by           UUID REFERENCES users(id),
    updated_by           UUID REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_lists (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    list_type   TEXT NOT NULL,
    description TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    source      TEXT NOT NULL DEFAULT 'MANUAL',
    created_by  UUID REFERENCES users(id),
    updated_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_list_entries (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    list_id            UUID NOT NULL REFERENCES player_lists(id) ON DELETE CASCADE,
    player_list_id     UUID REFERENCES player_lists(id) ON DELETE CASCADE,
    tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id          UUID REFERENCES players(id) ON DELETE SET NULL,
    external_player_id TEXT,
    cpf_hash           TEXT,
    value              TEXT,
    value_type         TEXT,
    notes              TEXT,
    added_by           UUID REFERENCES users(id),
    added_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scoring_configs (
    id                           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id                    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE UNIQUE,
    rule_weight                  DECIMAL(4,3) NOT NULL DEFAULT 0.4,
    ml_weight                    DECIMAL(4,3) NOT NULL DEFAULT 0.4,
    network_weight               DECIMAL(4,3) NOT NULL DEFAULT 0.2,
    auto_case_threshold          DECIMAL(5,4) NOT NULL DEFAULT 0.75,
    sla_critical_hours           INT NOT NULL DEFAULT 4,
    sla_high_hours               INT NOT NULL DEFAULT 24,
    sla_medium_hours             INT NOT NULL DEFAULT 72,
    sla_low_hours                INT NOT NULL DEFAULT 168,
    ingest_rate_limit_tpm        INT NOT NULL DEFAULT 1000,
    data_retention_raw_years     INT NOT NULL DEFAULT 5,
    data_retention_silver_years  INT NOT NULL DEFAULT 5,
    data_retention_gold_years    INT NOT NULL DEFAULT 3,
    is_active                    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by                   UUID REFERENCES users(id),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS system_flags (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    flag_name   TEXT NOT NULL,
    flag_value  JSONB NOT NULL DEFAULT 'false',
    updated_by  UUID REFERENCES users(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, flag_name)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id  UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id    UUID REFERENCES users(id),
    title      TEXT NOT NULL,
    body       TEXT,
    type       TEXT NOT NULL DEFAULT 'INFO',
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    read_at    TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id     UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    feature_date  DATE NOT NULL,
    snapshot_date DATE,
    features      JSONB NOT NULL DEFAULT '{}',
    drift_score   DECIMAL(5,4),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, player_id, feature_date)
);

CREATE TABLE IF NOT EXISTS ingest_errors (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ingest_job_id UUID REFERENCES ingest_jobs(id) ON DELETE SET NULL,
    source_system TEXT NOT NULL,
    entity_type   TEXT,
    raw_payload   TEXT NOT NULL,
    error_reason  TEXT NOT NULL,
    error_detail  JSONB NOT NULL DEFAULT '{}',
    line_number   INT,
    resolved      BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by   UUID REFERENCES users(id),
    resolved_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── 4. Row-Level Security ─────────────────────────────────────────────

-- Cria a função helper que extrai o tenant_id da configuração de sessão
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
$$ LANGUAGE SQL STABLE;

-- players
ALTER TABLE players ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS players_tenant_isolation ON players;
CREATE POLICY players_tenant_isolation ON players
    USING (tenant_id = current_tenant_id());

-- alerts
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS alerts_tenant_isolation ON alerts;
CREATE POLICY alerts_tenant_isolation ON alerts
    USING (tenant_id = current_tenant_id());

-- cases
ALTER TABLE cases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS cases_tenant_isolation ON cases;
CREATE POLICY cases_tenant_isolation ON cases
    USING (tenant_id = current_tenant_id());

-- audit_logs
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs;
CREATE POLICY audit_logs_tenant_isolation ON audit_logs
    USING (tenant_id = current_tenant_id());

-- rule_definitions
ALTER TABLE rule_definitions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rules_tenant_isolation ON rule_definitions;
CREATE POLICY rules_tenant_isolation ON rule_definitions
    USING (tenant_id = current_tenant_id());

-- report_packages
ALTER TABLE report_packages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS report_packages_tenant_isolation ON report_packages;
CREATE POLICY report_packages_tenant_isolation ON report_packages
    USING (tenant_id = current_tenant_id());

-- mapping_configs
ALTER TABLE mapping_configs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mapping_configs_tenant_isolation ON mapping_configs;
CREATE POLICY mapping_configs_tenant_isolation ON mapping_configs
    USING (tenant_id = current_tenant_id());

-- ingest_jobs
ALTER TABLE ingest_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS ingest_jobs_tenant_isolation ON ingest_jobs;
CREATE POLICY ingest_jobs_tenant_isolation ON ingest_jobs
    USING (tenant_id = current_tenant_id());

-- case_events
ALTER TABLE case_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS case_events_tenant_isolation ON case_events;
CREATE POLICY case_events_tenant_isolation ON case_events
    USING (tenant_id = current_tenant_id());

-- NOTAS IMPORTANTES:
-- 1. BYPASS RLS pelo superuser: o usuário 'betaml' (app user) NÃO é superuser;
--    O Postgres só aplica RLS para usuários não-superuser.
-- 2. O middleware da API faz SET app.current_tenant = '<uuid>' antes de cada query.
-- 3. Migrations e seeds devem ser executados via superuser (sem RLS).
--    Para permitir isso sem problemas, adicione:
--      ALTER TABLE <tabela> FORCE ROW LEVEL SECURITY;
--    apenas se quiser RLS mesmo para o owner da tabela.
-- 4. Seeds usam o mesmo user 'betaml' — para que seeds funcionem sem RLS,
--    cada INSERT no seed deve ser executado com SET LOCAL app.current_tenant = '<tenant_id>'
--    antes da inserção correspondente (o seeds.py já propaga via get_db).

-- ── 5. Índices adicionais ─────────────────────────────────────────────
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE compound_rules
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_report_packages_case    ON report_packages(case_id);
CREATE INDEX IF NOT EXISTS idx_report_packages_tenant  ON report_packages(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feature_snapshots_player ON feature_snapshots(tenant_id, player_id, feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_user      ON notifications(tenant_id, user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_compound_rules_tenant   ON compound_rules(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_player_lists_tenant     ON player_lists(tenant_id);
CREATE INDEX IF NOT EXISTS idx_scoring_config_tenant   ON scoring_configs(tenant_id);
