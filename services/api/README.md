# API Service

## Purpose
The central FastAPI backend for the BetAML platform. Provides authentication, event ingest, rule management, alert and case workflow, player profiles, feature store access, ML model registry, administrative controls, and COAF/LGPD compliance endpoints for Brazilian sports betting operators.

## Prerequisites
Docker + docker-compose OR Python 3.11+

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://betaml:devpass@localhost:5432/betaml_dev` | PostgreSQL 16 async connection string |
| `REDIS_URL` | `redis://:devpass@localhost:6379/0` | Redis 7 URL (rate limiting, JWT blacklist, feature cache) |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Redpanda / Kafka broker list |
| `JWT_SECRET` | `dev-secret-change-me` | **Must be changed in staging/production** |
| `EPSILON_WEBHOOK_SECRET` | `dev-secret-change-me` | HMAC secret for `ConnectorEpsilon` webhook validation |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MIN` | `60` | JWT expiry in minutes |
| `PII_ENCRYPTION_KEY` | `ZGV2LXNlY3...` | Fernet key for CPF/name encryption — **change in production** |
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO / S3 endpoint for document and model storage |
| `MINIO_ACCESS_KEY` | `minio` | MinIO access key |
| `MINIO_SECRET_KEY` | `minio123` | MinIO secret key |
| `MINIO_BUCKET` | `betaml-lakehouse` | Primary MinIO bucket name |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse host for analytics queries |
| `CLICKHOUSE_PORT` | `9000` | ClickHouse native protocol port |
| `CLICKHOUSE_DB` | `betaml` | ClickHouse database |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000` | Comma-separated allowed CORS origins (production) |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `DLQ_MAX_RETRIES` | `3` | Dead-letter queue max retry attempts |

## Running Locally
```bash
cd services/api
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Seed initial tenants and users manually when needed:
```bash
python seeds.py
```

In Docker Compose, automatic seeding is disabled by default in all environments. To force bootstrap in a controlled environment, set `API_AUTO_SEED=true` explicitly.

## Kafka Topics
| Direction | Topic | Description |
|---|---|---|
| Produced | `raw.transactions` | Raw transaction payloads (Bronze layer) |
| Produced | `raw.bets` | Raw bet payloads (Bronze layer) |
| Produced | `raw.device_events` | Raw device event payloads (Bronze layer) |
| Produced | `canonical.transactions` | Normalised transactions (Silver layer) |
| Produced | `canonical.bets` | Normalised bets (Silver layer) |
| Produced | `canonical.device_events` | Normalised device events (Silver layer) |
| Produced | `ingest.jobs` | Ingest job lifecycle events |
| Produced | `ingest.jobs.reprocess` | Reprocess triggers |
| Consumed | `scoring.alerts` | Optional legacy consumer. Disabled by default; the rules engine is the authoritative alert/case materializer |

## Key Endpoints
| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/auth/login` | Public | JWT login |
| `POST` | `/auth/logout` | Authenticated | Revoke current token (Redis blacklist) |
| `GET` | `/me` / `/auth/me` | Authenticated | Current user info |
| `POST` | `/ingest/event` | ADMIN / API Key | Ingest a single event, optionally applying a specific MappingConfig version |
| `POST` | `/ingest/batch` | ADMIN / API Key | Ingest multiple events, each optionally with explicit MappingConfig version |
| `POST` | `/ingest/file` | ADMIN / API Key | Upload a CSV/JSON file for batch ingest |
| `POST` | `/ingest/connectors/gamma/parse` | ADMIN / AML_ANALYST | Parse XML payloads from ConnectorGamma |
| `POST` | `/ingest/connectors/delta/parse` | ADMIN / AML_ANALYST | Parse NDJSON payloads from ConnectorDelta |
| `POST` | `/ingest/webhook/epsilon` | ADMIN / API Key | Receive HMAC-signed webhook payloads from ConnectorEpsilon |
| `GET` | `/ingest/jobs` | ADMIN / AML_ANALYST | Filter ingest jobs by status/source/date |
| `GET` | `/ingest/jobs/{id}` | ADMIN / AML_ANALYST | Detailed ingest job metrics and error sample |
| `POST` | `/ingest/jobs/{id}/reprocess` | ADMIN / AML_ANALYST | Re-read Bronze object using current/specific MappingConfig version |
| `GET` | `/ingest/errors` | ADMIN / AML_ANALYST | Quarantine list for mapping/parse failures |
| `POST` | `/ingest/errors/{id}/resolve` | ADMIN / AML_ANALYST | Mark quarantined item as resolved |
| `POST` | `/ingest/errors/{id}/replay` | ADMIN / AML_ANALYST | Replay corrected payload back to Kafka |
| `GET` | `/ingest/stream` | Authenticated | SSE ingest heartbeat / near-real-time operational stream |
| `WS` | `/ingest/ws` | ADMIN / AML_ANALYST | Continuous near-real-time ingest channel with backpressure and per-message MappingConfig |
| `GET` | `/mappings/templates` | Authenticated | Built-in YAML templates for Gamma/Delta/Epsilon |
| `POST` | `/mappings/validate` | ADMIN / AML_ANALYST | Validate mapping config against schema and canonical contract |
| `POST` | `/mappings/preview` | ADMIN / AML_ANALYST | Preview mapped result for sample payload |
| `GET` | `/mappings` | Authenticated | List immutable MappingConfig versions |
| `POST` | `/mappings` | ADMIN | Create new MappingConfig version chain |
| `PUT` | `/mappings/{id}` | ADMIN | Create a new immutable version of an existing mapping |
| `POST` | `/mappings/{id}/rollback` | ADMIN | Activate a previous version |
| `GET` | `/alerts` | AML_ANALYST+ | List alerts with filters |
| `PATCH` | `/alerts/{id}/label` | AML_ANALYST+ | Label an alert TRUE/FALSE positive |
| `GET` | `/alerts/{id}/explainability` | AML_ANALYST+ | Top 5 ML feature contributions with current vs baseline values |
| `GET` | `/cases` | AML_ANALYST+ | List investigation cases |
| `POST` | `/cases` | AML_ANALYST+ | Open a new case |
| `POST` | `/cases/{id}/assign` | ADMIN | Assign case to analyst |
| `POST` | `/cases/{id}/comments` | AML_ANALYST+ | Add comment with @mention notifications |
| `GET` | `/cases/{id}/lookup` | AML_ANALYST+ | Quick lookup for alerts and transactions to link into the case |
| `POST` | `/cases/{id}/link-alert` | AML_ANALYST+ | Link an existing alert to the case |
| `POST` | `/cases/{id}/link-transaction` | AML_ANALYST+ | Add a transaction reference to the case timeline |
| `POST` | `/cases/{id}/report-package` | AML_ANALYST+ | Generate/submit COAF SAR package |
| `GET` | `/cases/{id}/report-packages` | AML_ANALYST+ | Case report-package history |
| `GET` | `/cases/{id}/report-package/json` | AML_ANALYST+ | Export a specific report package as JSON |
| `GET` | `/cases/{id}/report-package/pdf` | AML_ANALYST+ | Export a specific report package as PDF |
| `POST` | `/cases/{id}/report-package/submit` | ADMIN | Submit latest FILE_SAR package with maker-checker control |
| `GET` | `/report-packages` | AML_ANALYST+ | Tenant-wide report-package history |
| `GET` | `/players` | AML_ANALYST+ | List players with risk scores |
| `POST` | `/players/{id}/erase` | ADMIN | LGPD Art. 18 data erasure |
| `GET` | `/rules` | ADMIN | List DSL rules |
| `POST` | `/rules` | ADMIN | Create rule |
| `POST` | `/rules/{rule_id}/simulate` | AML_ANALYST+ | Simulate with manual events or historical alert window |
| `GET` | `/rules/compound` | AML_ANALYST+ | List compound rules |
| `POST` | `/rules/compound` | AML_ANALYST+ | Create compound rule (`AND` / `OR` / `N_OF_M`) |
| `PUT` | `/rules/compound/{rule_id}` | AML_ANALYST+ | Update compound rule operator, severity mode and thresholds |
| `GET` | `/rules/macros` | AML_ANALYST+ | List reusable DSL macros |
| `POST` | `/rules/macros` | AML_ANALYST+ | Create reusable DSL macro |
| `GET` | `/player-lists` | AML_ANALYST+ | List tenant player lists |
| `GET` | `/player-lists/{list_id}` | AML_ANALYST+ | Player list detail |
| `PATCH` | `/player-lists/{list_id}` | AML_ANALYST+ | Update metadata/source/status of a player list |
| `GET` | `/player-lists/{list_id}/entries` | AML_ANALYST+ | List player list entries |
| `POST` | `/player-lists/{list_id}/entries` | AML_ANALYST+ | Bulk-add entries manually |
| `POST` | `/player-lists/{list_id}/upload-csv` | AML_ANALYST+ | Bulk-upload entries from CSV/text file |
| `DELETE` | `/player-lists/{list_id}/entries/{entry_id}` | AML_ANALYST+ | Remove a specific entry |
| `GET` | `/audit-logs` | ADMIN / AUDITOR | Immutable audit trail with filters `action`, `entity_type`, `entity_id`, `user_id`, `date_from`, `date_to`, `q`, `pii_only` |
| `GET` | `/reports/monthly-summary` | AML_ANALYST+ / AUDITOR | Monthly regulatory summary with alerts, cases, communications and TP/FP quality metrics |
| `GET` | `/reports/monthly-summary/csv` | AML_ANALYST+ / AUDITOR | CSV export of the monthly regulatory summary |
| `POST` | `/reports/monthly-summary` | ADMIN / AML_ANALYST | Queue background generation of a monthly compliance summary |
| `GET` | `/health` | Public | Aggregate service health (`postgres`, `redis`, `kafka`, `minio`, `clickhouse`, `ml_service`, `rules_engine`, `stream_processor`) |
| `GET` | `/health/ready` | Public | Readiness probe for orchestrators and infra dashboards |
| `GET` | `/admin/ops/summary` | ADMIN / AUDITOR | Operational summary with Kafka lag, ingest error rate, stale models and DLQ alerts |
| `PUT` | `/admin/maintenance-mode?enabled=` | ADMIN | Enable/disable tenant maintenance mode (503 outside `/health` and `/auth`) |
| `GET` | `/admin/users` | ADMIN | List tenant users |
| `POST` | `/admin/users` | ADMIN | Create user |
| `PATCH` | `/admin/users/{id}` | ADMIN | Update user role / active |
| `DELETE` | `/admin/users/{id}` | ADMIN | Deactivate user |
| `POST` | `/admin/users/{id}/reset-password` | ADMIN | Generate new random password |
| `POST` | `/admin/invite` | ADMIN | Generate 48h invite token |
| `GET` | `/admin/api-keys` | ADMIN | List API keys |
| `POST` | `/admin/api-keys` | ADMIN | Create API key |
| `GET` | `/admin/api-keys/{id}/usage` | ADMIN | Last 30-day daily usage counts |
| `GET` | `/admin/tenants` | SUPER_ADMIN | List all tenants |
| `POST` | `/admin/tenants` | SUPER_ADMIN | Onboard new tenant |
| `PATCH` | `/admin/tenants/{id}` | SUPER_ADMIN | Update tenant name / status |
| `GET` | `/model-registry` | AML_ANALYST+ | Model registry list with artifact, sample and dataset window metadata |
| `GET` | `/model-registry/{id}` | AML_ANALYST+ | Single model registry detail |
| `GET` | `/model-registry/performance/summary` | AML_ANALYST+ | TP/FP dashboard by period, rule and model |
| `GET` | `/model-registry/{id}/ab-metrics` | AML_ANALYST+ | Champion vs challenger metrics and timeline |
| `POST` | `/model-registry/{id}/challenger` | ADMIN | Mark a staging model as challenger |
| `POST` | `/model-registry/{id}/promote` | ADMIN | Promote challenger to champion |
| `GET` | `/notifications` | Authenticated | In-app notifications |
| `GET` | `/feature-store/players/{player_id}/current` | AML_ANALYST+ | Current feature snapshot |
| `GET` | `/feature-store/players/{player_id}/history` | AML_ANALYST+ | Historical feature snapshots |
| `GET` | `/feature-store/population-stats` | AML_ANALYST+ | Tenant-level feature statistics cache with `computed_at` |
| `GET` | `/feature-store/quality/latest` | AML_ANALYST+ | Latest tenant drift/null-rate summary for feature quality |
| `GET` | `/metrics` | Internal | Prometheus metrics |
| `GET` | `/health` | Public | Health check |

## Scheduled Jobs (APScheduler)
| Job | Schedule | Description |
|---|---|---|
| `risk_score_decay` | Daily 04:00 UTC | Decays player risk scores towards baseline |
| `lgpd_data_expiration` | Daily 05:00 UTC | Purges PII for players past the retention window |
| `sla_violations_check` | Every hour | Notifies analysts of cases approaching SLA deadline |
| `feature_population_stats` | Daily 06:00 UTC | Rebuilds tenant feature population baselines in Redis |
| `feature_store_maintenance` | Continuous (24h loop) | Detects null-rate spikes and feature drift, notifying ADMIN users |

## Security Notes
- `JWT_SECRET` and `PII_ENCRYPTION_KEY` must be rotated from their defaults before deploying to staging or production. The app will refuse to start with default secrets when `ENVIRONMENT` is not `development` or `test`.
- `EPSILON_WEBHOOK_SECRET` should be rotated independently from JWT signing and used only for ConnectorEpsilon webhook HMAC validation.
- Rate limiting is enforced globally via slowapi (1 000 req/min, 10 000 req/hr per tenant).
- All PII (CPF, full name) is encrypted at rest with AES-128-CBC + HMAC (Fernet).
- Audit log entries are append-only and tenant-scoped via Postgres Row-Level Security.
