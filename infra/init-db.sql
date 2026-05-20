-- ============================================================
-- BetAML — PostgreSQL Schema (OLTP)
-- ============================================================

-- Extensões
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ──────────────────────────────────────────────────
-- Tenants
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                TEXT NOT NULL UNIQUE,
    slug                TEXT NOT NULL UNIQUE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    settings            JSONB NOT NULL DEFAULT '{}',
    risk_score_threshold DECIMAL(5,2) NOT NULL DEFAULT 0.75,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- Users (RBAC)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    username    TEXT NOT NULL,
    email       TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role        TEXT NOT NULL,  -- validação de roles feita pela aplicação (auth.py)
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, username),
    UNIQUE (tenant_id, email)
);

-- ──────────────────────────────────────────────────
-- Players (referência OLTP — dados canônicos no lakehouse)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS players (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    external_player_id  TEXT NOT NULL,
    -- CPF e nome armazenados criptografados
    cpf_encrypted       BYTEA NOT NULL,
    name_encrypted      BYTEA NOT NULL,
    birth_date          DATE,
    pep_flag            BOOLEAN NOT NULL DEFAULT FALSE,
    declared_income_monthly DECIMAL(15,2),
    profession          TEXT,
    risk_score          DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    last_scored_at      TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, external_player_id)
);

-- ──────────────────────────────────────────────────
-- MappingConfig (conectores de ingestão)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mapping_configs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1.0',
    config_json     JSONB NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- IngestJob
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_system   TEXT NOT NULL,
    mapping_config_id UUID REFERENCES mapping_configs(id),
    file_name       TEXT,
    file_size_bytes BIGINT,
    file_path       TEXT,
    status          TEXT NOT NULL DEFAULT 'QUEUED'
                        CHECK (status IN ('QUEUED','PROCESSING','DONE','FAILED','PARTIAL')),
    total_records   INT,
    processed_records INT DEFAULT 0,
    failed_records  INT DEFAULT 0,
    error_message   TEXT,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- RuleDefinition
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rule_definitions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE','INACTIVE','DRAFT')),
    severity        TEXT NOT NULL DEFAULT 'MEDIUM'
                        CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    scope           TEXT NOT NULL DEFAULT 'TRANSACTION'
                        CHECK (scope IN ('TRANSACTION','BET','PLAYER','DEVICE_EVENT')),
    condition_dsl   TEXT NOT NULL,
    params          JSONB NOT NULL DEFAULT '{}',
    version         INT NOT NULL DEFAULT 1,
    created_by      UUID REFERENCES users(id),
    updated_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- Alerts
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id       UUID REFERENCES players(id),
    rule_id         UUID REFERENCES rule_definitions(id),
    alert_type      TEXT NOT NULL DEFAULT 'RULE'
                        CHECK (alert_type IN ('RULE','ANOMALY','COMPOSITE')),
    severity        TEXT NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status          TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','IN_REVIEW','CLOSED','FALSE_POSITIVE')),
    title           TEXT NOT NULL,
    description     TEXT,
    evidence        JSONB NOT NULL DEFAULT '{}',
    anomaly_score   DECIMAL(5,4),
    source_event_id TEXT,
    case_id         UUID,   -- preenchido ao vincular a um case
    triaged_by      UUID REFERENCES users(id),
    triaged_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- Cases
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cases (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id       UUID REFERENCES players(id),
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN','INVESTIGATING','PENDING_REVIEW','CLOSED','REPORTED')),
    severity        TEXT NOT NULL DEFAULT 'HIGH'
                        CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    assigned_to     UUID REFERENCES users(id),
    created_by      UUID REFERENCES users(id),
    closed_by       UUID REFERENCES users(id),
    closed_at       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- FK reversa alerts → cases
ALTER TABLE alerts ADD CONSTRAINT fk_alerts_case
    FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE SET NULL;

-- ──────────────────────────────────────────────────
-- CaseEvents (timeline: notas, decisões, uploads)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS case_events (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    case_id     UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    event_type  TEXT NOT NULL CHECK (event_type IN
                    ('NOTE','STATUS_CHANGE','EVIDENCE_UPLOAD','ASSIGNMENT','REPORT_GENERATED','REPORT_SUBMITTED')),
    content     JSONB NOT NULL DEFAULT '{}',
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- ReportPackages
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS report_packages (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    case_id     UUID NOT NULL REFERENCES cases(id),
    player_id   UUID REFERENCES players(id),
    payload     JSONB NOT NULL,
    format      TEXT NOT NULL DEFAULT 'JSON' CHECK (format IN ('JSON','CSV')),
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- RuleExecutionLog (auditoria de regras)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rule_execution_logs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    rule_id         UUID NOT NULL REFERENCES rule_definitions(id),
    rule_version    INT NOT NULL,
    source_event_id TEXT NOT NULL,
    player_id       UUID,
    matched         BOOLEAN NOT NULL,
    evaluation_ms   INT,
    context_snapshot JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- AuditLog
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id),
    user_id     UUID REFERENCES users(id),
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT,
    before      JSONB,
    after       JSONB,
    ip_address  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- ModelRegistry (ML)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS model_registry (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    model_name      TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    algorithm       TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,
    dataset_window_days INT,
    metrics         JSONB NOT NULL DEFAULT '{}',
    active          BOOLEAN NOT NULL DEFAULT FALSE,
    trained_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ──────────────────────────────────────────────────
-- Índices
-- ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_status   ON alerts(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_alerts_player          ON alerts(player_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created         ON alerts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cases_tenant_status    ON cases(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_player           ON cases(player_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant      ON audit_logs(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rule_exec_tenant_event ON rule_execution_logs(tenant_id, source_event_id);
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_tenant     ON ingest_jobs(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_players_tenant         ON players(tenant_id, external_player_id);
