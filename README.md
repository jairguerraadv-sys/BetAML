# BetAML — PLD/FT Intelligence Platform

**Multi-tenant SaaS para detecção de lavagem de dinheiro e financiamento ao terrorismo em operadoras de apostas fixas brasileiras.**

---

## Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                        BetAML Platform                          │
├──────────┬──────────────┬──────────────┬───────────────────────┤
│ Frontend │   API REST   │ Rules Engine │     ML Service        │
│ Next.js  │  FastAPI     │ DSL Eval     │  IsolationForest      │
│ :3000    │  :8000       │ Kafka cons.  │  :8001                │
└──────────┴──────┬───────┴──────┬───────┴───────────────────────┘
                  │              │
         ┌────────▼──────────────▼────────┐
         │     Redpanda (Kafka)  :9092    │
         │     raw.* → canonical.*        │
         │     → features.* → scoring.*  │
         └──────────┬─────────────────────┘
                    │
    ┌───────────────┼───────────────────────┐
    ▼               ▼                       ▼
PostgreSQL 16    ClickHouse 24         Redis 7
(OLTP :5432)    (OLAP :9900)       (Feature Store :6379)

                    ▼
             MinIO (S3 :9001)
          Bronze / Silver / Gold + Modelos ML
```

## Estrutura do Monorepo

```
BetAML/
├── libs/                   # Bibliotecas compartilhadas Python
│   ├── schemas.py          # Pydantic v2: CanonicalEvent, PlayerFeatures, AlertMessage
│   ├── dsl_parser.py       # DSL tokenizer + parser + evaluator
│   ├── clients.py          # Kafka, Redis, ClickHouse clients (async)
│   └── mapping.py          # MappingEngine + conectores BackofficeAlpha/Beta
│
├── infra/
│   ├── docker-compose.yml  # Stack completa (13 serviços)
│   ├── init-db.sql         # Schema PostgreSQL (15 tabelas)
│   ├── clickhouse-init.sql # Schema ClickHouse (6 tabelas)
│   └── configs/
│       └── redpanda-console.yaml
│
├── services/
│   ├── api/                # FastAPI — REST, Auth, RBAC, Seeds
│   ├── stream_processor/   # Kafka consumer → features → Redis + ClickHouse
│   ├── rules_engine/       # DSL evaluation → scoring.alerts
│   ├── ml_service/         # IsolationForest scoring + training (FastAPI :8001)
│   └── frontend/           # Next.js 14 (App Router + Tailwind)
│
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_dsl.py      # 12 regras seed + todos operadores/funções
    │   └── test_mapping.py  # BackofficeAlpha/Beta transform types
    └── integration/
        └── test_pipeline.py # Smoke tests E2E (requer stack)
```

---

## Quickstart

### Pré-requisitos

- Docker >= 24 e Docker Compose v2
- 6–8 GB RAM livre recomendado

### 1. Subir a stack

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 2. Verificar saúde (aguardar ~20s)

```bash
curl http://localhost:8000/health
# { "status": "ok", ... }
```

### 3. Login

```bash
curl -X POST http://localhost:8000/auth/login \
  -d "username=admin_a&password=admin123"
```

### 4. URLs dos serviços

| Serviço              | URL                                            |
|----------------------|------------------------------------------------|
| API REST (Swagger)   | http://localhost:8000/docs                     |
| Frontend             | http://localhost:3000                          |
| Redpanda Console     | http://localhost:8080                          |
| MinIO Console        | http://localhost:9001 (`minio` / `minio123`)   |
| ClickHouse HTTP      | http://localhost:8123                          |

---

## Testes Unitários (sem Docker)

```bash
pip install pytest pydantic python-dateutil structlog
pytest tests/unit/ -v
```

### Testes de integração (requerem stack rodando)

```bash
TEST_STACK_UP=1 pytest tests/integration/ -v
```

---

## DSL de Regras

```dsl
# Structuring
transaction.amount > 9000 and transaction.amount < 10000 and transaction.type == 'DEPOSIT'

# Anomalia estatística
zscore(features.deposit_sum_24h, features.baseline_deposit_avg_30d, features.baseline_deposit_std_30d) > 3

# Round-trip mismo dia
ratio(features.withdraw_sum_24h, features.deposit_sum_24h) > 0.95

# PEP com volume atípico
player.pepFlag == true and features.deposit_sum_7d > 50000

# Aposta desproporcional
bet.stakeAmount > player.declaredIncomeMonthly * 2
```

Funções: `zscore(value, mean, std)`, `ratio(a, b)`, `abs(v)`, `sum(a, b, ...)`

---

## Tenants Seed

| Tenant    | Usuário   | Senha      |
|-----------|-----------|------------|
| OperadorA | `admin_a` | `admin123` |
| OperadorB | `admin_b` | `admin123` |

Cada tenant possui: 1 ADMIN + 1 AML_ANALYST + 1 AUDITOR + 50 jogadores + 12 regras DSL ativas.

---

## Compliance & LGPD

- CPF e PII criptografados em repouso (XOR para dev → usar KMS em prod)
- Mascaramento de CPF nas respostas (apenas 2 últimos dígitos visíveis)
- `audit_logs` rastreia todas as ações com `actor_id`, IP e `before/after_state`
- RBAC: `ADMIN` · `AML_ANALYST` · `AUDITOR`
