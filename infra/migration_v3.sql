-- =============================================================================
-- BetAML — Migration v3
-- Tabelas OLTP: financial_transactions, bets, device_events
-- Row-Level Security (RLS) para isolamento de tenant no nível do banco
-- =============================================================================

-- ─── 1. financial_transactions ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS financial_transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id           UUID REFERENCES players(id) ON DELETE SET NULL,
    external_tx_id      TEXT,
    source_system       TEXT NOT NULL,
    type                TEXT NOT NULL,        -- DEPOSIT, WITHDRAWAL, CHARGEBACK, BONUS, ...
    amount              DECIMAL(15,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'BRL',
    status              TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING, SETTLED, FAILED, REVERSED
    payment_method      TEXT,
    payment_instrument  TEXT,                 -- token/hash do instrumento (nunca dados brutos)
    bank_account_hash   TEXT,                 -- hash SHA-256 do IBAN/conta (nunca dado bruto)
    source_event_id     TEXT,
    ingest_job_id       UUID REFERENCES ingest_jobs(id) ON DELETE SET NULL,
    raw_payload         JSONB DEFAULT '{}',
    occurred_at         TIMESTAMPTZ NOT NULL,
    settled_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ft_tenant_player      ON financial_transactions(tenant_id, player_id);
CREATE INDEX IF NOT EXISTS idx_ft_tenant_occurred    ON financial_transactions(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_ft_tenant_type_status ON financial_transactions(tenant_id, type, status);
CREATE INDEX IF NOT EXISTS idx_ft_bank_hash          ON financial_transactions(bank_account_hash) WHERE bank_account_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ft_instrument         ON financial_transactions(payment_instrument) WHERE payment_instrument IS NOT NULL;

-- ─── 2. bets ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id        UUID REFERENCES players(id) ON DELETE SET NULL,
    external_bet_id  TEXT,
    source_system    TEXT NOT NULL,
    bet_type         TEXT NOT NULL DEFAULT 'SPORTS', -- SPORTS, CASINO, VIRTUAL, POKER, ...
    stake_amount     DECIMAL(15,2) NOT NULL,
    potential_payout DECIMAL(15,2),
    actual_payout    DECIMAL(15,2),
    odds             DECIMAL(10,4),
    currency         TEXT NOT NULL DEFAULT 'BRL',
    status           TEXT NOT NULL DEFAULT 'OPEN',  -- OPEN, SETTLED, VOIDED, CASHOUT
    event_name       TEXT,
    market_name      TEXT,
    selection_name   TEXT,
    source_event_id  TEXT,
    ingest_job_id    UUID REFERENCES ingest_jobs(id) ON DELETE SET NULL,
    raw_payload      JSONB DEFAULT '{}',
    occurred_at      TIMESTAMPTZ NOT NULL,
    settled_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bets_tenant_player   ON bets(tenant_id, player_id);
CREATE INDEX IF NOT EXISTS idx_bets_tenant_occurred ON bets(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_bets_tenant_status   ON bets(tenant_id, status);

-- ─── 3. device_events ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    player_id       UUID REFERENCES players(id) ON DELETE SET NULL,
    external_evt_id TEXT,
    source_system   TEXT NOT NULL,
    action          TEXT NOT NULL,   -- LOGIN, LOGOUT, DEPOSIT_ATTEMPT, REGISTER, ...
    device_id       TEXT,
    device_type     TEXT,            -- MOBILE_IOS, MOBILE_ANDROID, DESKTOP, ...
    device_hash     TEXT,            -- fingerprint hash (SHA-256 do device_id)
    ip_address      TEXT,
    ip_hash         TEXT,            -- SHA-256 do IP (preserva privacidade)
    country_code    TEXT,
    user_agent      TEXT,
    session_id      TEXT,
    source_event_id TEXT,
    ingest_job_id   UUID REFERENCES ingest_jobs(id) ON DELETE SET NULL,
    raw_payload     JSONB DEFAULT '{}',
    occurred_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dev_tenant_player  ON device_events(tenant_id, player_id);
CREATE INDEX IF NOT EXISTS idx_dev_tenant_device  ON device_events(tenant_id, device_hash) WHERE device_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dev_tenant_ip      ON device_events(tenant_id, ip_hash) WHERE ip_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dev_tenant_occurred ON device_events(tenant_id, occurred_at DESC);

-- =============================================================================
-- Row-Level Security (RLS)
-- Garante isolamento de tenant no nível do banco de dados.
-- As aplicações devem executar:  SET app.current_tenant = '<uuid>';
-- antes de qualquer query nas tabelas protegidas.
-- =============================================================================

-- Habilitar RLS nas tabelas críticas
ALTER TABLE players              ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts               ENABLE ROW LEVEL SECURITY;
ALTER TABLE cases                ENABLE ROW LEVEL SECURITY;
ALTER TABLE case_events          ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs           ENABLE ROW LEVEL SECURITY;
-- Adicionar coluna weight em rule_definitions (adicionada em models.py)
ALTER TABLE rule_definitions ADD COLUMN IF NOT EXISTS weight DECIMAL(4,3) NOT NULL DEFAULT 0.5;

-- Adicionar compound_rule_id em alerts (FK para compound_rules)
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS compound_rule_id UUID REFERENCES compound_rules(id);

ALTER TABLE rule_definitions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_jobs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bets                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE device_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_packages      ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_registry       ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications        ENABLE ROW LEVEL SECURITY;

-- Políticas RLS: cada tenant vê apenas seus próprios dados.
-- FORCE ROW LEVEL SECURITY faz valer até para o owner da tabela.
CREATE POLICY tenant_isolation_players
    ON players USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_alerts
    ON alerts USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_cases
    ON cases USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_case_events
    ON case_events USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_audit_logs
    ON audit_logs USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_rule_definitions
    ON rule_definitions USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_ingest_jobs
    ON ingest_jobs USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_financial_transactions
    ON financial_transactions USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_bets
    ON bets USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_device_events
    ON device_events USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_report_packages
    ON report_packages USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_model_registry
    ON model_registry USING (tenant_id::text = current_setting('app.current_tenant', true));

CREATE POLICY tenant_isolation_notifications
    ON notifications USING (tenant_id::text = current_setting('app.current_tenant', true));

-- Tenants e users: sem RLS (usados no bootstrap e autenticação)
-- mas as queries de auth.py já fazem .where() por user_id explicitamente.
