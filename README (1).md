# BetAML: PLD/FT Enterprise para Operadores de Apostas

> **Plataforma multi-tenant "nível banco" de detecção de anomalias e conformidade para operadores de apostas de quota fixa no Brasil.**

## 🎯 Visão Geral

BetAML é um sistema **event-driven**, **big data ready**, projetado para:

- ✅ **Ingestão universal**: múltiplos backoffices → modelo canônico centralizado
- ✅ **Análise de risco não engessada**: regras parametrizáveis + ML + baseline + peer group
- ✅ **Case management**: alertas → investigação → ReportPackage JSON
- ✅ **Enterprise-ready**: multi-tenant, RBAC, LGPD, auditoria, escalabilidade

---

## 🚀 Quick Start (Local)

### Pré-requisitos
- Docker & Docker Compose 20.10+
- Python 3.11+ (para CLI opcional)
- Node.js 18+ (frontend)

### Rodar tudo localmente

```bash
# Clone e entre no repo
git clone <repo-url> betaml
cd betaml

# Suba infra + serviços
docker-compose -f infra/docker-compose.yml up -d

# Aguarde ~30s para Postgres, ClickHouse, Kafka ficarem prontos
# Verifique health
curl http://localhost:8000/health

# Veja logs
docker-compose -f infra/docker-compose.yml logs -f api

# Acesse:
# - Frontend: http://localhost:3000
# - API Docs: http://localhost:8000/docs
# - MinIO: http://localhost:9001 (admin / minio123)
# - ClickHouse UI: http://localhost:8123 (opcional)
```

### Seed data
Ao iniciar, o script `infra/init-db.sql` + `services/api/seeds.py` criam:
- 2 tenants: **OperadorA**, **OperadorB**
- 3 usuários/tenant (ADMIN, AML_ANALYST, AUDITOR)
- 50 players + 200 transações simuladas
- 30 regras padrão
- Cenários suspeitos (structuring, spike, device compartilhado, etc.)

```bash
# Após containers UP, rode seeds manualmente se necessário:
docker-compose -f infra/docker-compose.yml exec api python seeds.py
```

### Credenciais default (DEV ONLY)
```
Tenant A: operador_a / password123
Tenant B: operador_b / password123
Role ADMIN: admin_a / admin123
Role ANALYST: analyst_a / analyst123
Role AUDITOR: auditor_a / auditor123
```

---

## 📦 Estrutura de Diretórios

```
betaml/
├── README.md (este arquivo)
├── services/
│   ├── api/                      # FastAPI (autenticação, CRUD, ingestão)
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── routes/
│   │   ├── dependencies.py
│   │   ├── seeds.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── stream_processor/         # Consumer Kafka → Features
│   │   ├── main.py
│   │   ├── features.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── rules_engine/             # Consumer Kafka → DSL → Alerts
│   │   ├── main.py
│   │   ├── dsl_parser.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── ml_service/               # FastAPI scoring + treino
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── train.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── frontend/                 # Next.js
│       ├── app/
│       ├── package.json
│       ├── next.config.js
│       └── Dockerfile
├── libs/                         # Código compartilhado
│   ├── schemas.py               # Canonical event envelope
│   ├── models.py                # Pydantic models
│   ├── dsl_parser.py            # DSL parser
│   ├── clients.py               # Kafka, ClickHouse, Redis
│   └── __init__.py
├── infra/
│   ├── docker-compose.yml
│   ├── init-db.sql
│   ├── kafka-init.sh
│   ├── clickhouse-init.sql
│   └── configs/
│       ├── kafka.properties
│       └── clickhouse-config.xml
└── tests/
    ├── unit/
    ├── integration/
    └── conftest.py
```

---

## 🏛️ Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND (Next.js)                    │
│          (Dashboard, Alertas, Casos, Regras, Mappings)      │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS/REST
┌──────────────────────▼──────────────────────────────────────┐
│                   API LAYER (FastAPI)                       │
│         Auth, CRUD, Ingestão (file/event), Triage           │
└──────────────────────┬──────────────────────────────────────┘
     ┌────────────────┬────────────────┬─────────────────┐
     │                │                │                 │
     ▼                ▼                ▼                 ▼
  Kafka            Postgres          Redis            MinIO
(Events)        (Configs,RBAC,      (Online      (Lakehouse
              Cases, Audit)       Features)     Bronze/Silver)
     ▲                                          ▲
     │      ┌────────────────────────────────┐ │
     │      │   Event Processing Layer       │ │
     │      ├────────────────────────────────┤ │
     │      │ Stream Processor (Features)    │ │
     │      │ Rules Engine (DSL)             │ │
     │      │ ML Service (Scoring/Train)     │ │
     │      └────────────────────────────────┘ │
     │                                         │
     └─────────────────────────────────────────┘
           ▼
     ClickHouse (OLAP)
  (Queries rápidas, dashboards)
```

**Camadas:**
- **Bronze**: raw_payloads (como chegaram)
- **Silver**: eventos canônicos normalizados
- **Gold**: features agregadas (24h/7d/30d), baselines

---

## 🔄 Fluxos Principais

### Fluxo 1: Ingestão de Arquivo
```
1. POST /ingest/file (CSV/JSON + sourceSystem + mappingConfigId)
2. API valida → cria IngestJob (QUEUED) no Postgres
3. Publica "ingest.jobs" no Kafka
4. Worker lê arquivo → aplica MappingConfig
5. Produz raw.* e canonical.* tópicos
6. MinIO armazena Bronze/Silver
```

### Fluxo 2: Feature Computation (Stream)
```
1. Stream Processor consome canonical.transactions + canonical.bets
2. Calcula features em janelas (24h/7d/30d)
3. Armazena em Redis (online) e parquet (offline Gold)
4. Publica "features.player_updated" tópico
```

### Fluxo 3: Rules Evaluation
```
1. Rules Engine consome canonical.* + features
2. Avalia regras DSL ativas do tenant
3. Se match → gera Alert com evidências
4. Publica "scoring.alerts"
5. Escreve RuleExecutionLog (auditoria)
```

### Fluxo 4: ML Scoring
```
1. Stream Processor chama ML Service com features
2. ML retorna anomalyScore + top_drivers
3. Atualiza Alert com "ANOMALY" ou "COMPOSITE"
```

### Fluxo 5: Case Management
```
1. Alert com severity >= HIGH → auto-cria Case
2. Analista investiga, adiciona evidências/notas
3. Decisão (SAR/SAT/CLOSE) gera ReportPackage
4. ReportPackage pode ser exportado JSON/CSV
```

---

## 📊 Modelo de Dados (Canônico)

### Canonical Event Envelope
```json
{
  "eventId": "uuid",
  "tenantId": "uuid",
  "sourceSystem": "BackofficeAlpha",
  "sourceEventId": "BOFF-2024-001",
  "schemaVersion": 1,
  "entityType": "TRANSACTION",
  "occurredAt": "2024-02-26T10:30:00Z",
  "payload": { /* canônico */ },
  "rawPayload": { /* bruto original */ },
  "ingestMeta": {
    "receivedAt": "2024-02-26T10:31:00Z",
    "mapperVersion": "1.0",
    "checksum": "sha256-abc123"
  }
}
```

### Payloads Canônicos Mínimos

**PLAYER:**
```json
{
  "externalPlayerId": "BOFF-PLAYER-123",
  "cpf": "12345678901",
  "name": "João Silva",
  "birthDate": "1990-01-15",
  "pepFlag": false,
  "declaredIncomeMonthly": 5000,
  "profession": "Engineer"
}
```

**TRANSACTION:**
```json
{
  "externalTransactionId": "TXN-2024-001",
  "playerId": "player-uuid",
  "type": "DEPOSIT",
  "amount": 100.00,
  "currency": "BRL",
  "method": "PIX",
  "status": "SETTLED",
  "paymentInstrument": {
    "institutionCode": "33066",
    "holderDocument": "12345678901",
    "verifiedFlag": true
  },
  "occurredAt": "2024-02-26T10:30:00Z"
}
```

**BET:**
```json
{
  "externalBetId": "BET-2024-001",
  "playerId": "player-uuid",
  "stakeAmount": 50.00,
  "odds": 2.5,
  "potentialPayout": 125.00,
  "settledPayout": 0,
  "marketType": "MONEYLINE",
  "sport": "FOOTBALL",
  "eventId": "EVENT-2024-001",
  "selection": "HOME",
  "channel": "WEB",
  "placedAt": "2024-02-26T10:30:00Z",
  "settledAt": null
}
```

---

## 📡 Tópicos Kafka

| Tópico | Schema | Retenção | Partição |
|--------|--------|----------|----------|
| `raw.players` | Raw Player | 30d | tenant_id |
| `raw.transactions` | Raw Transaction | 90d | player_id |
| `raw.bets` | Raw Bet | 90d | player_id |
| `raw.device_events` | Raw Device | 30d | device_id |
| `canonical.players` | Canonical Player | 365d | tenant_id |
| `canonical.transactions` | Canonical Txn | 365d | player_id |
| `canonical.bets` | Canonical Bet | 365d | player_id |
| `canonical.device_events` | Canonical Device | 30d | device_id |
| `features.player_daily` | Player Features | 365d | player_id |
| `scoring.alerts` | Alert Event | 90d | tenant_id |
| `cases.events` | Case Event | 365d | case_id |
| `ingest.jobs` | Ingest Job | 30d | tenant_id |

---

## 🔐 RBAC e Multi-Tenancy

### Roles
- **ADMIN**: criar users, editar regras, acessar auditoria
- **AML_ANALYST**: triage de alertas, criar casos, editar notas
- **AUDITOR**: somente leitura, acesso irrestrito a AuditLog

### Isolamento
- `tenant_id` sempre derivado do JWT, nunca aceitável como parâmetro de client
- Todos os queries incluem `WHERE tenant_id = <jwt_tenant>`
- RLS (Row-Level Security) no Postgres para camada adicional

### PII Handling
- CPF mascarado no frontend por padrão (mostrar `XXX.XXX.XX9` + tooltip)
- Colunas sensíveis criptografadas em repouso (Postgres: `pgcrypto`)
- AuditLog registra QUEM acessou QUANDO

---

## 🚀 APIs Obrigatórias

### Authentication
```
POST   /auth/login
POST   /auth/refresh
POST   /auth/logout
GET    /me
```

### Ingestão
```
POST   /ingest/file
POST   /ingest/event
POST   /ingest/batch
GET    /ingest/jobs
GET    /ingest/jobs/{id}
```

### Regras
```
GET    /rules (filtros, paginação)
POST   /rules (criar)
PUT    /rules/{id}
DELETE /rules/{id}
POST   /rules/{id}/simulate (DSL test)
```

### Alertas
```
GET    /alerts (filtros: severity, status, player, period)
GET    /alerts/{id}
POST   /alerts/{id}/triage (status: ACK, CLOSE, ESCALATE)
POST   /alerts/{id}/link-to-case
```

### Casos
```
GET    /cases
POST   /cases
GET    /cases/{id}
PUT    /cases/{id} (status: OPEN, UNDER_INVESTIGATION, SAR, SAT, CLOSE)
POST   /cases/{id}/events (nota/decisão)
POST   /cases/{id}/evidence (upload arquivo)
POST   /cases/{id}/report-package (gera JSON)
```

### Mappings
```
GET    /mappings
POST   /mappings
PUT    /mappings/{id}
POST   /mappings/{id}/test-transform (valida com amostra)
```

### Audit
```
GET    /audit-logs (somente ADMIN/AUDITOR)
```

### Health
```
GET    /health
GET    /health/postgres
GET    /health/kafka
GET    /health/clickhouse
```

---

## 🎯 Rules Engine DSL

### Exemplo 1: Spike vs Baseline
```
IF features.zscore_current_deposit_vs_baseline > 3.0
AND features.deposit_sum_24h > 5000
THEN severity=HIGH, rule_category=SPIKE
```

### Exemplo 2: Structuring
```
IF count(transaction WHERE type=DEPOSIT AND amount < 1000 IN 24h) >= 5
AND sum(transaction WHERE type=DEPOSIT IN 24h) > 3000
THEN severity=MEDIUM, rule_category=STRUCTURING
```

### Exemplo 3: Conta Bancária Compartilhada
```
IF features.shared_bank_account_count > 2
AND player.pepFlag = true
THEN severity=HIGH, rule_category=PEP_RISK
```

---

## 📈 Features Disponíveis

**Financeiras:**
- `deposit_sum_24h`, `deposit_sum_7d`, `deposit_sum_30d`
- `withdrawal_sum_24h`, `withdrawal_sum_7d`, `withdrawal_sum_30d`
- `ratio_withdrawal_to_deposit_7d`

**Comportamentais:**
- `bet_stake_sum_24h`, `bet_stake_sum_7d`
- `new_payment_instrument_flag`
- `new_device_flag`

**Baseline:**
- `baseline_avg_daily_deposit`
- `baseline_stddev_deposit`
- `zscore_current_deposit_vs_baseline`

**Correlação:**
- `shared_device_count`
- `shared_bank_account_count`

---

## 🧪 Testes

### Rodar testes locais
```bash
# Unit tests
cd services/api && pytest tests/unit/ -v

# Integration tests (requer Docker UP)
pytest tests/integration/ -v --docker-compose=infra/docker-compose.yml

# Coverage
pytest --cov=services/api tests/
```

### Teste manual: Ingestão → Alertas
```bash
# 1. Crie um arquivo CSV de teste
cat > test_transactions.csv << 'EOF'
externalTransactionId,playerId,type,amount,method,status,occurredAt
TXN-001,PLAYER-001,DEPOSIT,10000,PIX,SETTLED,2024-02-26T10:00:00Z
TXN-002,PLAYER-001,DEPOSIT,10000,PIX,SETTLED,2024-02-26T10:05:00Z
EOF

# 2. Faça upload
curl -X POST http://localhost:8000/ingest/file \
  -H "Authorization: Bearer <token>" \
  -F "file=@test_transactions.csv" \
  -F "sourceSystem=BackofficeAlpha" \
  -F "mappingConfigId=<uuid>"

# 3. Acompanhe via /ingest/jobs
curl http://localhost:8000/ingest/jobs \
  -H "Authorization: Bearer <token>"

# 4. Verifique alertas gerados
curl http://localhost:8000/alerts?severity=HIGH \
  -H "Authorization: Bearer <token>"
```

---

## 📚 Documentação Adicional

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)**: Diagramas detalhados
- **[DSL_GUIDE.md](docs/DSL_GUIDE.md)**: Referência completa de DSL
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)**: Guia de produção
- **[LGPD_COMPLIANCE.md](docs/LGPD_COMPLIANCE.md)**: Conformidade com dados pessoais
- **[API_REFERENCE.md](docs/API_REFERENCE.md)**: OpenAPI gerado

---

## 🐛 Troubleshooting

### Containers não sobem
```bash
# Verifique se portas estão disponíveis
lsof -i :5432  # Postgres
lsof -i :9092  # Kafka
lsof -i :6379  # Redis

# Limpe volumes e tente novamente
docker-compose -f infra/docker-compose.yml down -v
docker-compose -f infra/docker-compose.yml up -d
```

### Alertas não aparecem
```bash
# Verifique se regras estão ativas
curl http://localhost:8000/rules?status=ACTIVE \
  -H "Authorization: Bearer <token>"

# Verifique logs do rules_engine
docker-compose -f infra/docker-compose.yml logs rules_engine

# Verifique features no Redis
docker-compose -f infra/docker-compose.yml exec redis \
  redis-cli KEYS "operador_a:*"
```

### Performance lenta em alertas
```bash
# Verifique tamanho do ClickHouse
SELECT table, formatReadableSize(total_bytes) as size
FROM system.tables
WHERE database = 'default'

# Reindex se necessário
OPTIMIZE TABLE scoring_alerts FINAL
```

---

## 📞 Suporte e Contribuições

- Issues: [GitHub Issues](https://github.com/betaml/betaml/issues)
- Docs: [Wiki](https://github.com/betaml/betaml/wiki)
- Email: support@betaml.io

---

## 📄 Licença

Propriedade da Equipe BetAML. Todos os direitos reservados (2024).

---

**Última atualização:** 26/02/2024 | **Versão:** 1.0.0-MVP
