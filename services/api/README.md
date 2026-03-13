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

Seed initial tenants and users:
```bash
python seeds.py
```

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
| Consumed | `scoring.alerts` | Alerts from Rules Engine consumed by the embedded Alert Processor |

## Key Endpoints
| Method | Path | Role | Description |
|---|---|---|---|
| `POST` | `/auth/login` | Public | JWT login |
| `POST` | `/auth/logout` | Authenticated | Revoke current token (Redis blacklist) |
| `GET` | `/auth/me` | Authenticated | Current user info |
| `POST` | `/ingest/event` | ADMIN / API Key | Ingest a single canonical event |
| `POST` | `/ingest/file` | ADMIN / API Key | Upload a CSV/JSON file for batch ingest |
| `GET` | `/alerts` | AML_ANALYST+ | List alerts with filters |
| `PATCH` | `/alerts/{id}/label` | AML_ANALYST+ | Label an alert TRUE/FALSE positive |
| `GET` | `/cases` | AML_ANALYST+ | List investigation cases |
| `POST` | `/cases` | AML_ANALYST+ | Open a new case |
| `POST` | `/cases/{id}/report-package/submit` | AML_ANALYST+ | Submit COAF SAR package |
| `GET` | `/players` | AML_ANALYST+ | List players with risk scores |
| `POST` | `/players/{id}/erase` | ADMIN | LGPD Art. 18 data erasure |
| `GET` | `/rules` | ADMIN | List DSL rules |
| `POST` | `/rules` | ADMIN | Create rule |
| `GET` | `/audit-logs` | ADMIN / AUDITOR | Immutable audit trail |
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
| `GET` | `/ml/models` | AML_ANALYST+ | Model registry list |
| `POST` | `/ml/models/{id}/promote` | ADMIN | Promote model to champion |
| `GET` | `/notifications` | Authenticated | In-app notifications |
| `GET` | `/feature-store/{player_id}` | AML_ANALYST+ | Current feature snapshot |
| `GET` | `/feature-store/{player_id}/history` | AML_ANALYST+ | Historical feature snapshots |
| `GET` | `/metrics` | Internal | Prometheus metrics |
| `GET` | `/health` | Public | Health check |

## Scheduled Jobs (APScheduler)
| Job | Schedule | Description |
|---|---|---|
| `risk_score_decay` | Daily 04:00 UTC | Decays player risk scores towards baseline |
| `lgpd_data_expiration` | Daily 05:00 UTC | Purges PII for players past the retention window |
| `sla_violations_check` | Every hour | Notifies analysts of cases approaching SLA deadline |

## Security Notes
- `JWT_SECRET` and `PII_ENCRYPTION_KEY` must be rotated from their defaults before deploying to staging or production. The app will refuse to start with default secrets when `ENVIRONMENT` is not `development` or `test`.
- Rate limiting is enforced globally via slowapi (1 000 req/min, 10 000 req/hr per tenant).
- All PII (CPF, full name) is encrypted at rest with AES-128-CBC + HMAC (Fernet).
- Audit log entries are append-only and tenant-scoped via Postgres Row-Level Security.
