-- ============================================================
-- BetAML — Migration v2 — Enterprise Features
-- Execute AFTER init-db.sql
-- ============================================================

-- ──────────────────────────────────────────────────
-- IngestErrors (quarentena)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_errors (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ingest_job_id     UUID REFERENCES ingest_jobs(id) ON DELETE SET NULL,
    source_system     TEXT NOT NULL,
    entity_type       TEXT,
    raw_payload       TEXT NOT NULL,
    error_reason      TEXT NOT NULL,
    error_detail      JSONB NOT NULL DEFAULT '{}',
    line_number       INT,
    resolved          BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by       UUID REFERENCES users(id),
    resolved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_job      ON ingest_errors(ingest_job_id);
CREATE INDEX IF NOT EXISTS idx_ingest_errors_tenant   ON ingest_errors(tenant_id, resolved);

-- ──────────────────────────────────────────────────
-- MappingConfig versioning (nova coluna + tabela de versões)
-- ──────────────────────────────────────────────────
ALTER TABLE mapping_configs ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES mapping_configs(id);
ALTER TABLE mapping_configs ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE mapping_configs ADD COLUMN IF NOT EXISTS version_number INT NOT NULL DEFAULT 1;
ALTER TABLE mapping_configs ADD COLUMN IF NOT EXISTS change_notes TEXT;

-- ──────────────────────────────────────────────────
-- ApiKeys
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    key_hash        TEXT NOT NULL UNIQUE,
    key_prefix      TEXT NOT NULL,   -- primeiros 8 chars para display
    source_system   TEXT,
    permissions     JSONB NOT NULL DEFAULT '["ingest"]',
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id),
    revoked_by      UUID REFERENCES users(id),
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON api_keys(key_hash);

-- ──────────────────────────────────────────────────
-- PlayerLists
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS player_lists (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    list_type   TEXT NOT NULL CHECK (list_type IN ('WHITELIST','BLACKLIST','WATCH_LIST','CUSTOM')),
    description TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    source      TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL','AUTO','INTEGRATION')),
    created_by  UUID REFERENCES users(id),
    updated_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS player_list_entries (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    list_id     UUID NOT NULL REFERENCES player_lists(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id   UUID REFERENCES players(id) ON DELETE SET NULL,
    external_player_id TEXT,
    cpf_hash    TEXT,               -- SHA-256 do CPF para lookup sem decriptação
    notes       TEXT,
    added_by    UUID REFERENCES users(id),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (list_id, cpf_hash)
);
CREATE INDEX IF NOT EXISTS idx_player_list_entries_list ON player_list_entries(list_id);
CREATE INDEX IF NOT EXISTS idx_player_list_entries_cpf  ON player_list_entries(cpf_hash);

-- ──────────────────────────────────────────────────
-- CompoundRules
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS compound_rules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE','DRAFT')),
    operator        TEXT NOT NULL DEFAULT 'AND' CHECK (operator IN ('AND','OR','N_OF_M')),
    n_threshold     INT,                     -- para N_OF_M
    child_rule_ids  JSONB NOT NULL DEFAULT '[]',   -- array de rule_definition ids
    severity_mode   TEXT NOT NULL DEFAULT 'MAX' CHECK (severity_mode IN ('MAX','MIN','FIXED')),
    fixed_severity  TEXT CHECK (fixed_severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    version         INT NOT NULL DEFAULT 1,
    created_by      UUID REFERENCES users(id),
    updated_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- DSL Macros
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rule_macros (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    body_dsl    TEXT NOT NULL,
    description TEXT,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, name)
);

-- ──────────────────────────────────────────────────
-- Scoring Config (pesos e thresholds por tenant)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scoring_configs (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id               UUID NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    rule_weight             DECIMAL(4,3) NOT NULL DEFAULT 0.4,
    ml_weight               DECIMAL(4,3) NOT NULL DEFAULT 0.4,
    network_weight          DECIMAL(4,3) NOT NULL DEFAULT 0.2,
    auto_case_threshold     DECIMAL(5,4) NOT NULL DEFAULT 0.75,
    sla_critical_hours      INT NOT NULL DEFAULT 4,
    sla_high_hours          INT NOT NULL DEFAULT 24,
    sla_medium_hours        INT NOT NULL DEFAULT 72,
    sla_low_hours           INT NOT NULL DEFAULT 168,
    ingest_rate_limit_tpm   INT NOT NULL DEFAULT 1000,   -- transactions/min por tenant
    data_retention_raw_years    INT NOT NULL DEFAULT 5,
    data_retention_silver_years INT NOT NULL DEFAULT 5,
    data_retention_gold_years   INT NOT NULL DEFAULT 3,
    updated_by              UUID REFERENCES users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- Notifications (in-app)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type        TEXT NOT NULL CHECK (type IN (
                    'ALERT_CRITICAL','CASE_ASSIGNED','SLA_WARNING',
                    'MODEL_DRIFT','FEATURE_DRIFT','DLQ_PENDING',
                    'MENTION','SYSTEM')),
    title       TEXT NOT NULL,
    body        TEXT,
    entity_type TEXT,
    entity_id   TEXT,
    read        BOOLEAN NOT NULL DEFAULT FALSE,
    read_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, read, created_at DESC);

-- ──────────────────────────────────────────────────
-- AlertLabels (feedback loop)
-- ──────────────────────────────────────────────────
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS label TEXT
    CHECK (label IN ('TRUE_POSITIVE','FALSE_POSITIVE','UNKNOWN'));
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS labeled_by  UUID REFERENCES users(id);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS labeled_at  TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule_weight DECIMAL(4,3)  DEFAULT 0.4;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS ml_weight   DECIMAL(4,3)  DEFAULT 0.4;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS network_weight DECIMAL(4,3) DEFAULT 0.2;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS composite_score DECIMAL(5,4);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS score_breakdown JSONB DEFAULT '{}';

-- ──────────────────────────────────────────────────
-- Cases: novos campos
-- ──────────────────────────────────────────────────
ALTER TABLE cases ADD COLUMN IF NOT EXISTS reference_number TEXT;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS sla_due_at     TIMESTAMPTZ;
ALTER TABLE cases ADD COLUMN IF NOT EXISTS priority       TEXT DEFAULT 'MEDIUM'
    CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL'));

-- Gera reference_number default para casos existentes
UPDATE cases SET reference_number = 'CASE-' || UPPER(SUBSTRING(id::TEXT, 1, 8))
WHERE reference_number IS NULL;

-- ──────────────────────────────────────────────────
-- ModelRegistry: campos adicionais
-- ──────────────────────────────────────────────────
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS model_type TEXT DEFAULT 'ANOMALY'
    CHECK (model_type IN ('ANOMALY','STRUCTURING','NETWORK','RECURRENCE'));
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'STAGING'
    CHECK (status IN ('STAGING','PRODUCTION','ARCHIVED'));
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS dataset_window_start TIMESTAMPTZ;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS dataset_window_end   TIMESTAMPTZ;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS sample_count         INT;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS promoted_by          UUID REFERENCES users(id);
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS promoted_at          TIMESTAMPTZ;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS is_challenger        BOOLEAN DEFAULT FALSE;
ALTER TABLE model_registry ADD COLUMN IF NOT EXISTS champion_id          UUID REFERENCES model_registry(id);

-- ──────────────────────────────────────────────────
-- IngestJob: campos adicionais
-- ──────────────────────────────────────────────────
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS bytes_processed BIGINT DEFAULT 0;
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS duration_ms     BIGINT;
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS error_sample    JSONB DEFAULT '[]';
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS connector_type  TEXT DEFAULT 'FILE'
    CHECK (connector_type IN ('FILE','WEBHOOK','WEBSOCKET','NDJSON','XML'));
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS reprocessed_from UUID REFERENCES ingest_jobs(id);
ALTER TABLE ingest_jobs ADD COLUMN IF NOT EXISTS mapping_version_id UUID REFERENCES mapping_configs(id);

-- ──────────────────────────────────────────────────
-- Maintenance mode (flag global)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_flags (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL DEFAULT 'null',
    updated_by  UUID REFERENCES users(id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO system_flags (key, value) VALUES ('maintenance_mode', 'false')
    ON CONFLICT (key) DO NOTHING;

-- ──────────────────────────────────────────────────
-- FeatureStore offline (Gold snapshots ref)
-- (dados reais ficam no ClickHouse/lakehouse)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS feature_snapshots (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id       UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    feature_date    DATE NOT NULL,
    features        JSONB NOT NULL DEFAULT '{}',
    drift_score     DECIMAL(5,4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, player_id, feature_date)
);
CREATE INDEX IF NOT EXISTS idx_feature_snapshots_player ON feature_snapshots(player_id, feature_date DESC);

-- ──────────────────────────────────────────────────
-- ReportPackage: PDF storage
-- ──────────────────────────────────────────────────
ALTER TABLE report_packages ADD COLUMN IF NOT EXISTS pdf_path      TEXT;
ALTER TABLE report_packages ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'DRAFT'
    CHECK (status IN ('DRAFT','FINAL','ARCHIVED'));
ALTER TABLE report_packages ADD COLUMN IF NOT EXISTS analyst_narrative TEXT;
ALTER TABLE report_packages ADD COLUMN IF NOT EXISTS decision TEXT
    CHECK (decision IN ('REPORT','CLOSE','MONITOR'));

-- ──────────────────────────────────────────────────
-- Players: campos adicionais
-- ──────────────────────────────────────────────────
ALTER TABLE players ADD COLUMN IF NOT EXISTS registered_since DATE;
ALTER TABLE players ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE','SUSPENDED','BANNED','CLOSED'));
ALTER TABLE players ADD COLUMN IF NOT EXISTS full_name TEXT;    -- cache decriptado (mascarado em exibição)
ALTER TABLE players ADD COLUMN IF NOT EXISTS external_id TEXT;  -- alias de external_player_id

-- ──────────────────────────────────────────────────
-- Índices adicionais
-- ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_player_lists_tenant       ON player_lists(tenant_id, list_type);
CREATE INDEX IF NOT EXISTS idx_notifications_unread      ON notifications(user_id) WHERE read = FALSE;
CREATE INDEX IF NOT EXISTS idx_cases_sla                 ON cases(tenant_id, sla_due_at) WHERE status NOT IN ('CLOSED','CLOSED_SAR','CLOSED_SAT');
CREATE INDEX IF NOT EXISTS idx_model_registry_tenant     ON model_registry(tenant_id, status);
