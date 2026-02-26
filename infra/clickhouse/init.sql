CREATE DATABASE IF NOT EXISTS betaml;

-- Transactions table (partitioned by tenant and date)
CREATE TABLE IF NOT EXISTS betaml.transactions (
    id UUID DEFAULT generateUUIDv4(),
    tenant_id UUID,
    player_id String,
    player_cpf String,
    external_transaction_id String DEFAULT '',
    type LowCardinality(String),
    amount Decimal(18, 2),
    currency LowCardinality(String) DEFAULT 'BRL',
    method LowCardinality(String),
    status LowCardinality(String),
    institution_code String DEFAULT '',
    holder_document String DEFAULT '',
    verified_flag UInt8 DEFAULT 0,
    occurred_at DateTime,
    source_system String DEFAULT '',
    event_id UUID,
    schema_version UInt8 DEFAULT 1,
    ingested_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY (tenant_id, toDate(occurred_at))
ORDER BY (tenant_id, player_id, occurred_at);

-- Bets table
CREATE TABLE IF NOT EXISTS betaml.bets (
    id UUID DEFAULT generateUUIDv4(),
    tenant_id UUID,
    player_id String,
    player_cpf String,
    external_bet_id String DEFAULT '',
    stake_amount Decimal(18, 2),
    odds Decimal(10, 4),
    potential_payout Decimal(18, 2),
    settled_payout Nullable(Decimal(18, 2)),
    market_type String DEFAULT '',
    sport String DEFAULT '',
    event_id_ext String DEFAULT '',
    selection String DEFAULT '',
    channel LowCardinality(String),
    placed_at DateTime,
    settled_at Nullable(DateTime),
    source_system String DEFAULT '',
    tenant_event_id UUID,
    ingested_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY (tenant_id, toDate(placed_at))
ORDER BY (tenant_id, player_id, placed_at);

-- Player features (time series)
CREATE TABLE IF NOT EXISTS betaml.player_features (
    tenant_id UUID,
    player_id String,
    feature_date Date,
    deposit_sum_24h Decimal(18,2) DEFAULT 0,
    deposit_sum_7d Decimal(18,2) DEFAULT 0,
    deposit_sum_30d Decimal(18,2) DEFAULT 0,
    deposit_count_24h UInt32 DEFAULT 0,
    deposit_count_7d UInt32 DEFAULT 0,
    withdrawal_sum_24h Decimal(18,2) DEFAULT 0,
    withdrawal_sum_7d Decimal(18,2) DEFAULT 0,
    bet_stake_sum_24h Decimal(18,2) DEFAULT 0,
    bet_stake_sum_7d Decimal(18,2) DEFAULT 0,
    ratio_withdrawal_to_deposit_7d Float64 DEFAULT 0,
    baseline_avg_daily_deposit Float64 DEFAULT 0,
    baseline_stddev_deposit Float64 DEFAULT 0,
    zscore_current_deposit_vs_baseline Float64 DEFAULT 0,
    new_payment_instrument_flag UInt8 DEFAULT 0,
    new_device_flag UInt8 DEFAULT 0,
    shared_device_count UInt32 DEFAULT 0,
    shared_bank_account_count UInt32 DEFAULT 0,
    updated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY (tenant_id, toYYYYMM(feature_date))
ORDER BY (tenant_id, player_id, feature_date);

-- Alerts (OLAP copy)
CREATE TABLE IF NOT EXISTS betaml.alerts (
    id UUID,
    tenant_id UUID,
    player_id String,
    player_cpf String DEFAULT '',
    rule_id Nullable(UUID),
    alert_type LowCardinality(String),
    severity LowCardinality(String),
    status LowCardinality(String),
    risk_score Float64 DEFAULT 0,
    created_at DateTime,
    updated_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY (tenant_id, toYYYYMM(created_at))
ORDER BY (tenant_id, created_at, severity);
