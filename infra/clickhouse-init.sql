-- ============================================================
-- BetAML — ClickHouse Schema (OLAP)
-- ============================================================

CREATE DATABASE IF NOT EXISTS betaml;

-- ──────────────────────────────────────────────────
-- Canonical Events (Silver)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.canonical_events (
    event_id            String,
    tenant_id           String,
    source_system       String,
    source_event_id     String,
    schema_version      UInt8,
    entity_type         LowCardinality(String),
    occurred_at         DateTime,
    event_date          Date DEFAULT toDate(occurred_at),
    payload             String,    -- JSON
    ingest_received_at  DateTime,
    created_at          DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(event_date))
ORDER BY (tenant_id, entity_type, occurred_at, event_id)
TTL occurred_at + INTERVAL 365 DAY;

-- ──────────────────────────────────────────────────
-- Transactions (Silver — desnormalizado para OLAP)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.transactions (
    event_id            String,
    tenant_id           String,
    source_system       String,
    source_event_id     String,
    player_id           String,
    transaction_type    LowCardinality(String),
    amount              Decimal(18,2),
    currency            LowCardinality(String) DEFAULT 'BRL',
    method              LowCardinality(String),
    status              LowCardinality(String),
    occurred_at         DateTime,
    event_date          Date DEFAULT toDate(occurred_at),
    created_at          DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(event_date))
ORDER BY (tenant_id, player_id, occurred_at)
TTL occurred_at + INTERVAL 365 DAY;

-- ──────────────────────────────────────────────────
-- Bets (Silver)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.bets (
    event_id        String,
    tenant_id       String,
    source_system   String,
    player_id       String,
    stake_amount    Decimal(18,2),
    odds            Nullable(Decimal(10,4)),
    potential_payout Nullable(Decimal(18,2)),
    settled_payout  Nullable(Decimal(18,2)),
    market_type     String,
    sport           String,
    channel         LowCardinality(String),
    placed_at       DateTime,
    settled_at      Nullable(DateTime),
    event_date      Date DEFAULT toDate(placed_at),
    status          LowCardinality(String),
    created_at      DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(event_date))
ORDER BY (tenant_id, player_id, placed_at)
TTL placed_at + INTERVAL 365 DAY;

-- ──────────────────────────────────────────────────
-- Player Features Gold (diário)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.player_features_daily (
    tenant_id               String,
    player_id               String,
    feature_date            Date,
    deposit_sum_24h         Decimal(18,2),
    deposit_sum_7d          Decimal(18,2),
    deposit_sum_30d         Decimal(18,2),
    deposit_count_24h       UInt32,
    deposit_count_7d        UInt32,
    withdrawal_sum_24h      Decimal(18,2),
    withdrawal_sum_7d       Decimal(18,2),
    withdrawal_count_24h    UInt32,
    bet_stake_sum_24h       Decimal(18,2),
    bet_stake_sum_7d        Decimal(18,2),
    ratio_w2d_7d            Decimal(10,4),
    baseline_avg_deposit    Decimal(18,2),
    baseline_stddev_deposit Decimal(18,2),
    zscore_deposit          Decimal(10,4),
    new_payment_flag        UInt8,
    new_device_flag         UInt8,
    shared_device_count     UInt32,
    shared_bank_count       UInt32,
    chargeback_count_30d    UInt32,
    feature_version         UInt8 DEFAULT 1,
    computed_at             DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY (tenant_id, toYYYYMM(feature_date))
ORDER BY (tenant_id, player_id, feature_date);

-- ──────────────────────────────────────────────────
-- Scoring Alerts (denormalized for dashboards)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.scoring_alerts (
    alert_id        String,
    tenant_id       String,
    player_id       String,
    rule_id         String,
    alert_type      LowCardinality(String),
    severity        LowCardinality(String),
    status          LowCardinality(String) DEFAULT 'OPEN',
    title           String,
    anomaly_score   Nullable(Float32),
    evidence        String,   -- JSON
    created_at      DateTime,
    alert_date      Date DEFAULT toDate(created_at)
)
ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(alert_date))
ORDER BY (tenant_id, created_at DESC, alert_id)
TTL created_at + INTERVAL 90 DAY;

-- ──────────────────────────────────────────────────
-- Rule Execution Logs (auditoria OLAP)
-- ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS betaml.rule_execution_logs (
    log_id          String,
    tenant_id       String,
    rule_id         String,
    rule_version    UInt8,
    source_event_id String,
    player_id       String,
    matched         UInt8,
    evaluation_ms   UInt32,
    created_at      DateTime,
    log_date        Date DEFAULT toDate(created_at)
)
ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(log_date))
ORDER BY (tenant_id, created_at, rule_id)
TTL created_at + INTERVAL 90 DAY;
