# BetAML

SaaS multi-tenant PLD/FT ("nível banco") system for fixed-odds betting operators in Brazil. Built for Big Data: high volume, high event rate, strong audit trail.

## Architecture

Event-driven, layered architecture:

| Layer | Technology | Purpose |
|---|---|---|
| API | FastAPI | Auth, CRUD, ingest endpoints |
| Event Bus | Redpanda (Kafka-compatible) | Async event streaming |
| Data Lakehouse | MinIO (S3-compatible) | Bronze/Silver/Gold parquet storage |
| OLAP | ClickHouse | Fast analytics queries |
| OLTP | PostgreSQL | Workflow entities, RBAC |
| Stream Processing | Python consumer | Feature computation |
| Rules Engine | Python consumer | DSL rule evaluation |
| ML Service | FastAPI + scikit-learn | IsolationForest anomaly detection |
| Frontend | Next.js 14 | Enterprise UI |
| Cache | Redis | Online feature store, token blacklist |

## Quick Start

### Prerequisites
- Docker Desktop or Docker Engine + Docker Compose v2

### 1. Clone and start
```bash
git clone <repo>
cd BetAML

# Start all services
cd infra
docker compose up -d --build

# Wait for services to be healthy (~2 minutes)
docker compose ps
```

### 2. Run database migrations
```bash
docker compose exec api alembic upgrade head
```

### 3. Seed data
```bash
docker compose run --rm seed
```

### 4. Access services
| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API (Swagger) | http://localhost:8000/docs |
| ML Service | http://localhost:8001/docs |
| Redpanda Console | http://localhost:8080 |
| MinIO Console | http://localhost:9001 |
| ClickHouse | http://localhost:8123 |

### 5. Login credentials (seed data)
| Email | Password | Role | Tenant |
|---|---|---|---|
| admin@operadora.com | Admin123! | ADMIN | OperadorA |
| analyst@operadora.com | Analyst123! | AML_ANALYST | OperadorA |
| auditor@operadora.com | Auditor123! | AUDITOR | OperadorA |
| admin@operadorb.com | Admin123! | ADMIN | OperadorB |

## Services

### `services/api` (port 8000)
FastAPI application. Key endpoints:
- `POST /auth/login` - JWT authentication
- `POST /ingest/file` - File upload ingestion
- `POST /ingest/batch` - Batch event ingestion
- `GET /alerts` - List alerts with filters
- `GET /cases` - List cases
- `POST /rules` - Create rule with DSL
- `GET /audit-logs` - Audit log (ADMIN/AUDITOR)

### `services/stream_processor`
Kafka consumer that computes player features and writes to ClickHouse + Redis + MinIO.

### `services/rules_engine`
Kafka consumer that evaluates DSL rules and creates alerts.

### `services/ml_service` (port 8001)
- `POST /train` - Train IsolationForest model for tenant
- `POST /score` - Score player features

### `services/frontend` (port 3000)
Next.js 14 with: Login, Dashboard, Alerts, Cases, Rules, Mapping Configs.

## DSL Reference

Rules use a simple DSL:

```
# Comparisons
features.deposit_sum_24h > 1000
transaction.amount >= 5000
player.pepFlag == true

# Logical
features.zscore_current_deposit_vs_baseline > 3.0 and features.deposit_count_24h > 5

# Functions
zscore(features.deposit_sum_24h, features.baseline_avg_daily_deposit, features.baseline_stddev_deposit) > 2.5
ratio(features.withdrawal_sum_7d, features.deposit_sum_7d) > 0.9
```

## Data Model

### Canonical Event Envelope
All events flowing through Kafka follow this schema:
```json
{
  "eventId": "uuid",
  "tenantId": "uuid",
  "sourceSystem": "BackofficeAlpha",
  "sourceEventId": "ext-123",
  "schemaVersion": 1,
  "entityType": "TRANSACTION",
  "occurredAt": "2024-01-01T10:00:00Z",
  "payload": { "...canonical fields..." },
  "rawPayload": { "...original data..." },
  "ingestMetadata": { "receivedAt": "...", "mapperVersion": "1.0" }
}
```

### Kafka Topics
- `raw.transactions` / `raw.bets` / `raw.players` / `raw.device_events`
- `canonical.transactions` / `canonical.bets` / `canonical.players` / `canonical.device_events`
- `features.player_daily`
- `scoring.alerts`
- `cases.events`
- `ingest.jobs`

## Multi-Tenant & Security
- `tenant_id` always derived from JWT token, never from client input
- RBAC: ADMIN > AML_ANALYST > AUDITOR (read-only)
- CPF masked in UI (last 2 digits only by default)
- Full audit log for all state changes
- Idempotent event processing via (tenant_id, source_system, source_event_id)

## Running Tests
```bash
cd tests
pip install -r requirements.txt
pytest unit/ -v
pytest integration/ -v
```

## Project Structure
```
BetAML/
├── libs/                    # Shared Python libraries
│   ├── schemas/             # Pydantic canonical models
│   ├── dsl/                 # DSL parser & evaluator
│   ├── transforms/          # Mapping transforms
│   └── clients/             # Kafka, Redis, S3, ClickHouse clients
├── services/
│   ├── api/                 # FastAPI service (port 8000)
│   ├── stream_processor/    # Feature computation consumer
│   ├── rules_engine/        # Rule evaluation consumer
│   ├── ml_service/          # ML training + scoring (port 8001)
│   └── frontend/            # Next.js UI (port 3000)
├── infra/
│   ├── docker-compose.yml   # Full local stack
│   ├── clickhouse/init.sql  # OLAP schema
│   ├── postgres/init.sql    # OLTP extensions
│   └── seed/                # Seed data scripts
└── tests/
    ├── unit/                # Unit tests (DSL, transforms)
    └── integration/         # Integration tests
```
