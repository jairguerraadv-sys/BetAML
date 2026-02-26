-- ClickHouse Schema Initialization for BetAML
-- OLAP database for fast queries on alerts, events, and aggregates

-- ===================== TABLES =====================

-- Canonical Events (immutable event log)
CREATE TABLE IF NOT EXISTS canonical_events (
    event_id UUID,
    tenant_id UUID,
    source_system String,
    source_event_id String,
    schema_version Int32,
    entity_type String,
    occurred_at DateTime,
    received_at DateTime DEFAULT now(),
    payload String,  -- JSON
    raw_payload String,  -- JSON
    mapper_version String,
    checksum String
) ENGINE = MergeTree()
ORDER BY (tenant_id, occurred_at, entity_type)
PARTITION BY toYYYYMM(occurred_at)
TTL occurred_at + INTERVAL 365 DAY;

-- Scoring Alerts
CREATE TABLE IF NOT EXISTS scoring_alerts (
    alert_id UUID,
    tenant_id UUID,
    player_id String,
    player_cpf String,
    severity String,
    status String,
    alert_type String,  -- RULE, ANOMALY, COMPOSITE
    rule_id UUID,
    anomaly_score Float32,
    evidence String,  -- JSON
    triggered_rules Array(String),
    created_at DateTime DEFAULT now(),
    updated_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, created_at, severity)
PARTITION BY toYYYYMM(created_at)
TTL created_at + INTERVAL 90 DAY;

-- Case Events
CREATE TABLE IF NOT EXISTS case_events (
    case_id UUID,
    tenant_id UUID,
    player_id String,
    case_status String,
    severity String,
    risk_score Float32,
    event_count Int32,
    created_at DateTime DEFAULT now(),
    updated_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, created_at)
PARTITION BY toYYYYMM(created_at)
TTL created_at + INTERVAL 365 DAY;

-- Rule Execution Logs
CREATE TABLE IF NOT EXISTS rule_execution_logs (
    event_id UUID,
    tenant_id UUID,
    rule_id UUID,
    rule_name String,
    matched UInt8,
    execution_time_ms Float32,
    executed_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, executed_at, rule_id)
PARTITION BY toYYYYMM(executed_at)
TTL executed_at + INTERVAL 90 DAY;

-- Player Features (daily aggregates)
CREATE TABLE IF NOT EXISTS player_features_daily (
    tenant_id UUID,
    player_id String,
    feature_date Date,
    
    -- Financial features
    deposit_sum_24h Float32,
    deposit_sum_7d Float32,
    deposit_sum_30d Float32,
    deposit_count_24h Int32,
    deposit_count_7d Int32,
    
    withdrawal_sum_24h Float32,
    withdrawal_sum_7d Float32,
    withdrawal_sum_30d Float32,
    withdrawal_count_24h Int32,
    withdrawal_count_7d Int32,
    
    -- Behavioral features
    bet_stake_sum_24h Float32,
    bet_stake_sum_7d Float32,
    bet_count_24h Int32,
    bet_count_7d Int32,
    
    -- Ratios
    ratio_withdrawal_to_deposit_7d Float32,
    
    -- Baseline
    baseline_avg_daily_deposit Float32,
    baseline_stddev_deposit Float32,
    zscore_current_deposit_vs_baseline Float32,
    
    -- Flags
    new_payment_instrument_flag UInt8,
    new_device_flag UInt8,
    
    -- Correlations
    shared_device_count Int32,
    shared_bank_account_count Int32,
    
    computed_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, feature_date, player_id)
PARTITION BY toYYYYMM(feature_date)
TTL feature_date + INTERVAL 365 DAY;

-- Transaction Summary (hourly)
CREATE TABLE IF NOT EXISTS transactions_hourly (
    tenant_id UUID,
    player_id String,
    transaction_type String,
    transaction_status String,
    payment_method String,
    
    bucket_hour DateTime,
    transaction_count Int32,
    amount_sum Float64,
    amount_min Float64,
    amount_max Float64,
    amount_avg Float64
) ENGINE = MergeTree()
ORDER BY (tenant_id, bucket_hour, player_id)
PARTITION BY toYYYYMM(bucket_hour)
TTL bucket_hour + INTERVAL 90 DAY;

-- Bet Summary (hourly)
CREATE TABLE IF NOT EXISTS bets_hourly (
    tenant_id UUID,
    player_id String,
    market_type String,
    sport String,
    channel String,
    
    bucket_hour DateTime,
    bet_count Int32,
    stake_sum Float64,
    stake_min Float64,
    stake_max Float64,
    stake_avg Float64,
    payout_sum Float64
) ENGINE = MergeTree()
ORDER BY (tenant_id, bucket_hour, player_id)
PARTITION BY toYYYYMM(bucket_hour)
TTL bucket_hour + INTERVAL 90 DAY;

-- Device Events Summary
CREATE TABLE IF NOT EXISTS device_events_hourly (
    tenant_id UUID,
    device_id String,
    player_ids Array(String),
    
    bucket_hour DateTime,
    event_count Int32,
    unique_players Int32,
    countries Array(String)
) ENGINE = MergeTree()
ORDER BY (tenant_id, bucket_hour, device_id)
PARTITION BY toYYYYMM(bucket_hour)
TTL bucket_hour + INTERVAL 30 DAY;

-- Alert Statistics (for dashboards)
CREATE TABLE IF NOT EXISTS alert_statistics_daily (
    tenant_id UUID,
    statistic_date Date,
    
    total_alerts Int32,
    critical_alerts Int32,
    high_alerts Int32,
    medium_alerts Int32,
    low_alerts Int32,
    
    new_alerts Int32,
    acked_alerts Int32,
    closed_alerts Int32,
    escalated_alerts Int32,
    
    unique_players Int32,
    avg_severity_score Float32,
    avg_anomaly_score Float32,
    
    computed_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, statistic_date)
PARTITION BY toYYYYMM(statistic_date)
TTL statistic_date + INTERVAL 365 DAY;

-- ===================== MATERIALIZED VIEWS =====================

-- Real-time alert count by severity
CREATE MATERIALIZED VIEW IF NOT EXISTS alerts_by_severity_mv
TO scoring_alerts
AS SELECT
    alert_id,
    tenant_id,
    player_id,
    player_cpf,
    severity,
    status,
    alert_type,
    rule_id,
    anomaly_score,
    evidence,
    triggered_rules,
    created_at,
    updated_at
FROM scoring_alerts;

-- ===================== FUNCTIONS =====================

-- Dictionary for tenant lookup
CREATE DICTIONARY IF NOT EXISTS tenant_dict (
    tenant_id UUID,
    tenant_name String
)
PRIMARY KEY tenant_id
SOURCE(CLICKHOUSE(QUERY 'SELECT id AS tenant_id, name AS tenant_name FROM tenants'))
LAYOUT(HASHED());

-- ===================== QUERIES (for reference) =====================

-- Top 10 players by alert count (last 30 days)
-- SELECT player_id, COUNT(*) as alert_count
-- FROM scoring_alerts
-- WHERE created_at > now() - INTERVAL 30 DAY AND tenant_id = <tenant_uuid>
-- GROUP BY player_id
-- ORDER BY alert_count DESC
-- LIMIT 10;

-- Alerts by severity over time
-- SELECT
--     toStartOfDay(created_at) as day,
--     severity,
--     COUNT(*) as count
-- FROM scoring_alerts
-- WHERE tenant_id = <tenant_uuid> AND created_at > now() - INTERVAL 30 DAY
-- GROUP BY day, severity
-- ORDER BY day DESC, severity;

-- Player risk profile
-- SELECT
--     player_id,
--     COUNT(*) as alert_count,
--     MAX(anomaly_score) as max_anomaly_score,
--     AVG(anomaly_score) as avg_anomaly_score
-- FROM scoring_alerts
-- WHERE tenant_id = <tenant_uuid> AND created_at > now() - INTERVAL 90 DAY
-- GROUP BY player_id
-- HAVING alert_count > 5;
