# Stream Processor

## Purpose
Consumes canonical events from Kafka and computes rolling feature windows (1h / 24h / 7d / 30d / 90d) per player. Writes the online feature store to Redis and the Gold layer to ClickHouse, then publishes `features.player_daily` and candidate `scoring.alerts`.

## Prerequisites
Docker + docker-compose OR Python 3.11+

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka / Redpanda broker addresses |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for the online feature store |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse host for the Gold layer |
| `CLICKHOUSE_PORT` | `9000` | ClickHouse native protocol port |
| `CLICKHOUSE_DB` | `betaml` | ClickHouse database name |

## Running Locally
```bash
cd services/stream_processor
pip install -r requirements.txt
python main.py
```

## Kafka Topics
| Direction | Topic | Description |
|---|---|---|
| Consumed | `canonical.transactions` | Normalised financial transactions (Silver layer) |
| Consumed | `canonical.bets` | Normalised bet events (Silver layer) |
| Consumed | `canonical.device_events` | Login / device fingerprint events |
| Consumed | `ingest.jobs` | Ingest job lifecycle signals |
| Consumed | `ingest.jobs.reprocess` | Reprocess triggers for failed ingests |
| Produced | `features.player_daily` | Aggregated daily feature snapshots per player |
| Produced | `scoring.alerts` | Candidate alerts for the Alert Processor |

## Feature Windows
The processor maintains Redis Sorted Sets keyed as `betaml:{tenant_id}:txn:{player_id}` (score = Unix timestamp, TTL = 90 days) and derives:

- **Deposit / Withdrawal aggregates**: sum and count over 1h, 24h, 7d, 30d
- **Baseline statistics**: incremental mean and standard deviation for Z-score computation
- **Behavioural flags**: new device, new payment instrument, shared device/account count
- **Derived ratios**: cashout ratio, night activity, weekend activity, chargeback rate
- **Network / graph features**: cluster membership and shared instrument score

All features are written back to Redis (online store, TTL 4h) and flushed to ClickHouse `player_features_daily` for historical analysis.
