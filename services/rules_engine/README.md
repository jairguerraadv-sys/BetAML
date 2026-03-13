# Rules Engine

## Purpose
Evaluates tenant-defined DSL rules against canonical events in real time. For each event it loads active rules from Postgres (cached in Redis, refreshed every 5 minutes), evaluates every rule against the event context plus the player's online features, and publishes an alert to `scoring.alerts` for each rule match.

## Prerequisites
Docker + docker-compose OR Python 3.11+

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka / Redpanda broker addresses |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for the online feature store and rule cache |
| `DATABASE_URL` | `postgresql://betaml:devpass@localhost:5432/betaml_dev` | PostgreSQL connection string for loading rules |

## Running Locally
```bash
cd services/rules_engine
pip install -r requirements.txt
python main.py
```

## Kafka Topics
| Direction | Topic | Description |
|---|---|---|
| Consumed | `canonical.transactions` | Normalised financial transactions |
| Consumed | `canonical.bets` | Normalised bet events |
| Consumed | `features.player_daily` | Daily feature snapshots (used to refresh online feature cache) |
| Produced | `scoring.alerts` | Alert messages for each triggered rule |

## Rule Evaluation
Rules are authored in BetAML's DSL (`condition_dsl` column in `rule_definitions`). The engine supports:

- **Simple rules**: arithmetic comparisons, boolean flags, string equality
- **DSL functions**: `zscore()`, `ratio()`, `is_in_list()`
- **Macro expansion**: reusable DSL fragments from the `rule_macros` table
- **Compound rules**: AND / OR / N-of-M combinations of simple rules with weighted scoring
- **Player lists**: blocklists / watchlists referenced by name from the DSL

On a rule match the engine writes an `Alert` row and a `RuleExecutionLog` row to Postgres via an async queue (separate DB writer task) and updates the player's `risk_score` and `risk_band` in-place.

Rule cache TTL is 300 seconds (configurable via `RULE_CACHE_TTL` constant).
