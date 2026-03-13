# BetAML — PLD/FT Intelligence Platform

**Multi-tenant SaaS para detecção de lavagem de dinheiro e financiamento ao terrorismo em operadoras de apostas fixas brasileiras.**

Versão: **2.1.0** · Compatível com: COAF Res. 36/2021 · LGPD Lei 13.709/2018 · Bacen Circular 3.978/2020

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
         │     ingest.jobs (CSV pipeline) │
         └──────────┬─────────────────────┘
                    │
    ┌───────────────┼───────────────────────┐
    ▼               ▼                       ▼
PostgreSQL 16    ClickHouse 24         Redis 7
(OLTP :5432)    (OLAP :9900)    (Feature Store + JWT Blacklist)
  RLS ativo                           :6379

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
│   ├── clients.py          # Kafka, Redis, ClickHouse clients (async) + Sorted Set helpers
│   └── mapping.py          # MappingEngine + conectores BackofficeAlpha/Beta
│
├── infra/
│   ├── docker-compose.yml  # Stack completa (13 serviços)
│   ├── init-db.sql         # Schema PostgreSQL base (tabelas core)
│   ├── migration_v2.sql    # Colunas adicionais (pdf_path, etc.)
│   ├── migration_v3.sql    # RLS + políticas de isolamento v3
│   ├── migration_v4.sql    # ★ RLS completo + todas as tabelas enterprise
│   ├── migration_v5.sql    # Tabelas enterprise adicionais (CompoundRule, PlayerList, etc.)
│   ├── migration_v6.sql    # scoring_configs: low/medium/high/critical_threshold + is_active
│   ├── clickhouse-init.sql # Schema ClickHouse (6 tabelas)
│   └── configs/
│       └── redpanda-console.yaml
│
├── services/
│   ├── api/                # FastAPI — REST, Auth JWT+Blacklist, RBAC, Seeds
│   ├── stream_processor/   # Kafka consumer → features Redis Sorted Sets + ingest.jobs
│   ├── rules_engine/       # DSL evaluation → scoring.alerts
│   ├── ml_service/         # IsolationForest scoring + training (FastAPI :8001)
│   └── frontend/           # Next.js 14 (App Router + Tailwind)
│
└── tests/
    ├── unit/
    │   ├── test_api_auth.py  # JWT jti, PII Fernet, RBAC, DSL, MappingEngine, Features
    │   ├── test_dsl.py       # 12 regras seed + todos operadores/funções DSL
    │   └── test_mapping.py   # BackofficeAlpha/Beta transform types
    └── integration/
        └── test_pipeline.py  # Smoke tests E2E + File Ingest + COAF + Logout/Blacklist
```

      ## Documentacao Operacional

      - `docs/ops-guide.md` - operacao da stack, migracoes e troubleshooting base
      - `docs/go-live-checklist.md` - checklist de release e validacao pos-deploy
      - `docs/slo-sli.md` - objetivos de confiabilidade e error budget
      - `docs/runbook-incidentes.md` - procedimentos de resposta a incidentes
      - `docs/aml-scorecard.md` - scorecard operacional AML (triagem, qualidade e SLA)
      - `docs/branch-protection.md` - padrao de branch protection e checks obrigatorios
      - `infra/grafana/provisioning/dashboards/betaml-reliability-slo.json` - painel de acompanhamento de SLO/SLI
      - `.github/workflows/release-readiness.yml` - gate manual de readiness com Alembic + migracao legada + E2E smoke
      - `.github/workflows/data-quality.yml` - gate diario de qualidade de dados AML
      - `.github/workflows/capacity-smoke.yml` - smoke semanal de capacidade com Locust

---

## Quickstart

### Pré-requisitos

- Docker >= 24 e Docker Compose v2
- 6–8 GB RAM livre recomendado

### 1. Subir a stack

```bash
docker compose -f infra/docker-compose.yml up -d
```

> **Nota:** As migrações SQL são executadas automaticamente na inicialização do container
> PostgreSQL na ordem: `init-db.sql` → `migration_v2.sql` → `migration_v3.sql` → `migration_v4.sql`
> → `migration_v5.sql` → `migration_v6.sql` → `migration_v7.sql` → `migration_v8.sql`
> → `migration_v9.sql` → `migration_v10.sql` → `migration_v11.sql` → `migration_v12.sql`
> → `migration_v13.sql`.
> O `migration_v4.sql` ativa as políticas **RLS** em todas as tabelas sensíveis.
> O `migration_v6.sql` adiciona colunas de threshold (`low_threshold`, `medium_threshold`, etc.)
> à tabela `scoring_configs`.
>
> **Se o volume PostgreSQL ja existia** e voce precisa aplicar upgrades incrementalmente,
> use o script idempotente abaixo (recomendado):
>
> `bash scripts/postgres_migrate_existing.sh`
>
> Para apenas visualizar o plano sem aplicar:
>
> `bash scripts/postgres_migrate_existing.sh --dry-run`

### Trilha formal com Alembic (baseline)

O projeto agora inclui baseline Alembic em `services/api/alembic/` para evolucao transacional de schema.

```bash
cd services/api

# Ver revisoes disponiveis
alembic -c alembic.ini heads

# Base nova (vazia): aplica ate o baseline
alembic -c alembic.ini upgrade head

# Base existente (ja com schema aplicado por SQL): apenas marca baseline
alembic -c alembic.ini stamp 20260313_000001
```

Durante a transicao, mantenha `scripts/postgres_migrate_existing.sh` como fallback para ambientes legados.

### 2. Verificar saúde (aguardar ~20s)

```bash
curl http://localhost:8000/health
# { "status": "ok", ... }
```

### 3. Login (JSON — não form-urlencoded)

Login básico:
```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123"}' | jq .
```

Login com `tenant_slug` explícito (recomendado em produção para garantir isolamento):
```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123", "tenant_slug": "operador_a"}' | jq .
```

### 4. Autenticação e logout

O token JWT inclui um campo `jti` único. O logout revoga o token na blacklist Redis
(TTL = tempo restante do token), impedindo seu reuso mesmo que não tenha expirado:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123"}' | jq -r .access_token)

# Usar o token
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me

# Logout real (invalida o token no Redis)
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/auth/logout

# Token agora retorna 401
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
```

### 5. Testes E2E (Playwright)

```bash
cd e2e
npm ci

# Configure variaveis locais de E2E (uma vez)
cp .env.e2e.example .env.e2e

# Executa smoke principal (auth + cases)
npx playwright test tests/auth.spec.ts tests/cases.spec.ts
```

Observacoes:
- `e2e/.env.e2e` e local e nao deve ser versionado.
- O Playwright carrega automaticamente `e2e/.env.e2e` e usa `e2e/.env.e2e.example` como fallback.

### 5. Ingestão de arquivo CSV (pipeline completo)

```bash
# Upload de CSV de transações
curl -s -X POST http://localhost:8000/ingest/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@transactions.csv;type=text/csv" \
  -F "source_system=BackofficeAlpha" \
  -F "entity_type=transaction" | jq .

# Verificar status do job
JOB_ID=<job_id retornado acima>
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/ingest/jobs/$JOB_ID | jq .
```

### 6. Gerar ReportPackage COAF

```bash
CASE_ID=<id do caso>

# Relatório DRAFT (decisão pendente)
curl -s -X POST http://localhost:8000/cases/$CASE_ID/report-package \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' | jq .

# Comunicação ao COAF (FILE_SAR — requer analyst_narrative obrigatório)
curl -s -X POST http://localhost:8000/cases/$CASE_ID/report-package \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "analyst_narrative": "Padrão de depósitos fracionados abaixo do limite de comunicação automática (Structuring, COAF/FATF ML-01). Recomenda-se comunicação imediata ao COAF.",
    "decision": "FILE_SAR"
  }' | jq .
```

### 7. URLs dos serviços

| Serviço              | URL                                            |
|----------------------|------------------------------------------------|
| API REST (Swagger)   | http://localhost:8000/docs                     |
| Frontend             | http://localhost:3000                          |
| Redpanda Console     | http://localhost:8080                          |
| MinIO Console        | http://localhost:9001 (`minio` / `minio123`)   |
| ClickHouse HTTP      | http://localhost:8123                          |

---

## Testes

### Unitários (sem Docker, rápido)

```bash
pip install -r requirements-dev.txt
pytest tests/unit/ -v
```

Cobrem: JWT (`jti`, expiração, isolamento), PII Fernet (encrypt/decrypt/mask), RBAC,
validação DSL (12 regras seed), MappingEngine, compute_features (estrutura, velocidade, moeda).

### Integração (requerem stack rodando)

```bash
docker compose -f infra/docker-compose.yml up -d
TEST_STACK_UP=1 pytest tests/integration/ -v --tb=short
```

Cobrem: ingestão de eventos/CSV, polling de job status, isolamento multi-tenant (RLS),
geração de ReportPackage COAF, logout/blacklist JWT, audit log.

### Qualidade de dados AML (automatizado)

```bash
python scripts/data_quality_checks.py
```

O workflow diario `Data Quality Gate` executa os checks de consistencia no PostgreSQL.

### Teste de capacidade (Locust)

```bash
locust -f tests/load/locustfile.py --host http://localhost:8000 --headless --users 20 --spawn-rate 5 --run-time 120s --only-summary
```

O workflow semanal `Capacity Smoke` roda esse smoke automaticamente.

---

## DSL de Regras

```dsl
# Structuring
transaction.amount > 9000 and transaction.amount < 10000 and transaction.type == 'DEPOSIT'

# Anomalia estatística
zscore(features.deposit_sum_24h, features.baseline_deposit_avg_30d, features.baseline_deposit_std_30d) > 3

# Round-trip mesmo dia
ratio(features.withdraw_sum_24h, features.deposit_sum_24h) > 0.95

# PEP com volume atípico
player.pepFlag == true and features.deposit_sum_7d > 50000

# Aposta desproporcional
bet.stakeAmount > player.declaredIncomeMonthly * 2
```

Funções disponíveis: `zscore(value, mean, std)`, `ratio(a, b)`, `abs(v)`, `sum(a, b, ...)`

---

## Tenants Seed

Após o primeiro `docker compose up`, o seed é aplicado automaticamente. Credenciais:

| Tenant    | Usuário       | Senha        | Role         |
|-----------|---------------|--------------|--------------|
| OperadorA | `admin_a`     | `admin123`   | ADMIN        |
| OperadorA | `analyst_a`   | `analyst123` | AML_ANALYST  |
| OperadorA | `auditor_a`   | `auditor123` | AUDITOR      |
| OperadorB | `admin_b`     | `admin123`   | ADMIN        |
| OperadorB | `analyst_b`   | `analyst123` | AML_ANALYST  |
| OperadorB | `auditor_b`   | `auditor123` | AUDITOR      |

Cada tenant possui: **3 usuários** (ADMIN + AML_ANALYST + AUDITOR) + **50 jogadores** (3 PEP) + **12 regras DSL** ativas + **4 alertas suspeitos** + **1 case auto-criado** + **ScoringConfig** + **2 PlayerLists** + **2 CompoundRules**.

> **Atenção:** Credenciais de **desenvolvimento** geradas pelo seed. Em staging/produção, troque
> todas as senhas e configure `JWT_SECRET` e `PII_ENCRYPTION_KEY` únicos no arquivo `.env`.

---

## Segurança & Compliance

### Isolamento multi-tenant
- **Row Level Security (RLS)** ativo em todas as tabelas sensíveis via `migration_v4.sql`
- Variável `app.current_tenant` injetada por middleware RLS no início de cada request
- Vazamento entre tenants resulta em 404 (não-existência opaca)

### Autenticação & Sessão
- JWT assimétrico com campo `jti` único por token
- Logout revoga o `jti` no Redis com TTL = tempo restante do token (blacklist real)
- Roles: `ADMIN` · `AML_ANALYST` · `AUDITOR`

### PII & LGPD (Lei 13.709/2018)
- CPF e dados pessoais cifrados em repouso com **Fernet AES-128 + HMAC-SHA256** (IV aleatório por registro)
- Mascaramento nas respostas: `***.***.***.09` (apenas os 2 últimos dígitos)
- Nunca expor CPF completo em logs, payloads de relatório ou respostas de API

### Relatórios COAF (Res. 36/2021)
- `POST /cases/{id}/report-package` gera estrutura JSON mínima compatível com COAF
- Campo `decision`: `FILE_SAR` | `NO_ACTION` | `PENDING`
- `FILE_SAR` exige `analyst_narrative` (Art. 9 Res. 36/2021) — validado pelo backend
- Todos os reports persistidos com `created_by` (UUID do analista), nunca username/email

### Auditoria
- `audit_logs` registra todas as ações mutantes com `user_id`, `entity_type`, `entity_id`,
  `before`, `after`, `ip_address` e `created_at` (schema canônico, sem campo `actor`)

