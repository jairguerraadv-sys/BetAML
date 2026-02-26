# BetAML Architecture Guide

## 1. System Overview

BetAML é uma plataforma event-driven, big data-ready para detecção de anomalias e conformidade (PLD/FT) em operadores de apostas. A arquitetura é baseada em:

- **Event Streaming**: Kafka como event bus central
- **Data Lakehouse**: MinIO (S3) com camadas Bronze/Silver/Gold
- **OLAP**: ClickHouse para queries rápidas
- **OLTP**: PostgreSQL para entidades e configurações
- **Feature Store**: Redis (online) + Parquet (offline)
- **ML**: Isolation Forest com versionamento e explicabilidade

## 2. Fluxo de Dados Completo

```
┌─ Backoffice Alpha ─┐    ┌─ Backoffice Beta ──┐    ┌─ API Manual ─┐
│                    │    │                     │    │              │
│  CSV/JSON/REST     │    │  CSV/JSON/REST      │    │  REST Events │
└─────────┬──────────┘    └──────────┬──────────┘    └──────┬───────┘
          │                          │                       │
          └──────────────────────────┼───────────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │      API (FastAPI)              │
                    │   - Validate                    │
                    │   - MappingConfig               │
                    │   - Create IngestJob            │
                    │   - RBAC Check                  │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │    raw.* Topics (Kafka)        │
                    │  - raw.transactions            │
                    │  - raw.bets                    │
                    │  - raw.device_events           │
                    └────────────────┬────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │  MinIO - Bronze Layer          │
                    │  (Raw payloads as-is)          │
                    │  Partitioned by:               │
                    │  - tenant_id                   │
                    │  - event_date                  │
                    │  - source_system               │
                    └────────────────┬────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
        ▼                            ▼                            ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Stream Processor │      │  Rules Engine    │      │ Stream Processor │
│  - Denormalize   │      │  - Load Rules    │      │  - Aggregate     │
│  - Apply Mapping │      │  - Eval DSL      │      │  - Compute Feat  │
│  - to Canonical  │      │  - Gen Alerts    │      │  - Update Redis  │
└────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘
         │                         │                         │
         │ canonical.*             │ scoring.alerts          │ features.player_daily
         │                         │                         │
         ▼                         ▼                         ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ MinIO - Silver   │      │ ClickHouse       │      │ Redis + MinIO    │
│ (Normalized)     │      │ (Online OLAP)    │      │ (Online + Offline)
│ Parquet          │      │ - Dashboards     │      │ - ML Scoring     │
│ Partitioned      │      │ - Investigations │      │ - Feature Lookup │
└──────────────────┘      └──────────────────┘      └──────────────────┘
         │                         │                         │
         │                         ▼                         │
         │                  PostgreSQL                       │
         │                   - Alerts                        │
         │                   - Cases                         │
         │                   - Audit Logs                    │
         │                                                   │
         └─────────────────────────┬───────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │   ML Service        │
                        │  - Scoring          │
                        │  - Training         │
                        │  - Model Registry   │
                        └────────┬────────────┘
                                 │
                        ┌────────▼────────┐
                        │  Frontend       │
                        │  (Next.js)      │
                        │  - Dashboards   │
                        │  - Alerts       │
                        │  - Cases        │
                        │  - Rules        │
                        └─────────────────┘
```

## 3. Componentes Principais

### 3.1 API (FastAPI)
- **Autenticação**: JWT
- **RBAC**: ADMIN, AML_ANALYST, AUDITOR
- **Ingestão**: file upload, event batch, single event
- **CRUD**: Rules, Mappings, Cases, Alerts
- **Isolation**: tenant_id sempre do JWT, nunca do client

### 3.2 Stream Processor
- Consome: canonical.transactions, canonical.bets, canonical.device_events
- Computa: features em janelas 24h/7d/30d
- Armazena: Redis (cache, TTL 1h) + MinIO (offline, long-term)
- Publica: features.player_daily

### 3.3 Rules Engine
- Consome: canonical.transactions, canonical.bets
- Carrega: rules do Postgres (cache em Redis)
- Avalia: DSL customizado
- Gera: scoring.alerts com evidências

### 3.4 ML Service
- POST /score: anomaly detection em tempo real
- POST /train: treino diário offline
- Model Registry: versionamento no Postgres
- Artefatos: S3/MinIO

## 4. Modelo Canônico

Todos os eventos seguem **CanonicalEventEnvelope**:

```json
{
  "eventId": "uuid",
  "tenantId": "uuid",
  "sourceSystem": "BackofficeAlpha",
  "sourceEventId": "BOFF-2024-001",
  "schemaVersion": 1,
  "entityType": "TRANSACTION",
  "occurredAt": "2024-02-26T10:30:00Z",
  
  "payload": {
    "playerId": "uuid",
    "amount": 100.00,
    "type": "DEPOSIT",
    ...
  },
  
  "rawPayload": { /* original */ },
  
  "ingestMeta": {
    "receivedAt": "2024-02-26T10:31:00Z",
    "mapperVersion": "1.0",
    "checksum": "sha256-..."
  }
}
```

## 5. Tópicos Kafka

| Tópico | Descrição | Retenção | Partição |
|--------|-----------|----------|----------|
| raw.* | Payloads originais | 30d | entity |
| canonical.* | Eventos normalizados | 365d | player_id |
| features.player_daily | Agregados diários | 365d | player_id |
| scoring.alerts | Alertas gerados | 90d | tenant_id |
| cases.events | Eventos de caso | 365d | case_id |
| ingest.jobs | Status de ingestão | 30d | tenant_id |

## 6. Database Schema (PostgreSQL)

**Tabelas principais:**
- `tenants`: multi-tenancy root
- `users`: RBAC (role, permissions)
- `mapping_configs`: regras de transformação
- `rule_definitions`: DSL rules versionadas
- `alerts`: alertas gerados
- `cases`: agrupamento de alertas
- `audit_logs`: trilha completa
- `model_registry`: versões de modelos ML

**Índices**: tenant_id + status/date para queries rápidas
**RLS**: Row-Level Security para isolamento de tenant (opcional)

## 7. Feature Store

### Online (Redis)
- Key: `{tenant_id}:{player_id}:features`
- Value: JSON com 15+ features
- TTL: 1 hora
- Usado por: Rules Engine, ML Scoring

### Offline (MinIO/Parquet)
- Path: `s3://betaml-lakehouse/Gold/features/{tenant_id}/{date}/`
- Formato: Parquet particionado por data
- Retenção: 365 dias
- Usado por: ML Training, Auditoria

## 8. Rules Engine DSL

**Exemplo 1: Spike vs Baseline**
```
IF features.zscore_current_deposit_vs_baseline > 3.0
AND features.deposit_sum_24h > 5000
THEN severity=HIGH, rule_category=SPIKE
```

**Exemplo 2: Structuring**
```
IF count(transaction WHERE type=DEPOSIT AND amount < 1000 IN 24h) >= 5
AND sum(transaction WHERE type=DEPOSIT IN 24h) > 3000
THEN severity=MEDIUM, rule_category=STRUCTURING
```

**Suporta:**
- Operadores: `>`, `<`, `>=`, `<=`, `==`, `!=`, `in`, `contains`
- Lógica: `and`, `or`, `not`
- Funções: `sum()`, `count()`, `avg()`, `zscore()`, `ratio()`
- Acesso: `features.*`, `player.*`, `transaction.*`

## 9. Fluxo de Alerta → Caso

1. **Alert gerado** por regra ou ML
   - Armazenado em Postgres + ClickHouse
   - Publicado em `scoring.alerts`

2. **Auto-escalação para Case** se:
   - severity >= HIGH, OU
   - riskScore >= threshold do tenant, OU
   - correlação de múltiplos alertas em X horas

3. **Investigação**: Analista
   - Visualiza timeline de eventos
   - Upload de evidências
   - Adiciona notas
   - Define decisão: SAR/SAT/CLOSE

4. **ReportPackage gerado**
   - JSON com dados (mascarado em UI)
   - Lista de eventos relevantes
   - Regras disparadas + evidências
   - Justificativa do analista
   - Exportável como JSON/CSV

## 10. Observabilidade

### Logs Estruturados
```json
{
  "timestamp": "2024-02-26T10:30:00Z",
  "level": "INFO",
  "service": "rules_engine",
  "tenant_id": "uuid",
  "event_id": "uuid",
  "message": "Rule matched",
  "context": { ... }
}
```

### Métricas Prometheus
- `alerts_generated_total`: contador por tenant/severity
- `rule_evaluation_duration_ms`: latência por regra
- `features_computed_total`: counter por tenant
- `kafka_consumer_lag`: lag dos consumers

### Health Checks
- `/health`: service ready
- `/health/postgres`: DB connectivity
- `/health/kafka`: broker connectivity
- `/health/redis`: cache connectivity

## 11. Deployment Checklist

- [ ] PostgreSQL com 3+ replicas
- [ ] Kafka com 3+ brokers
- [ ] Redis com 2+ instâncias (master-slave)
- [ ] ClickHouse com 2+ nodes
- [ ] MinIO com 4+ discos (erasure code)
- [ ] API com 2+ replicas (load balancer)
- [ ] Stream Processor com 3+ partições
- [ ] Rules Engine com 3+ partições
- [ ] ML Service com 2+ replicas
- [ ] Backups: Postgres (diário), MinIO (semanal)
- [ ] Monitoramento: Prometheus + Grafana
- [ ] Logging: ELK ou similar
- [ ] Alerting: PagerDuty, Slack

## 12. Performance e Escalabilidade

### Throughput esperado (single region)
- **Eventos**: 10k-100k por segundo (dependendo de Kafka brokers)
- **Alertas**: 1k-10k por segundo (dependendo de regras)
- **Queries**: 100 req/sec (ClickHouse, bem indexado)

### Scaling
- **Horizontal**: adicione mais Kafka partições, replicas de Stream Processor/Rules Engine
- **Vertical**: aumentar CPU/RAM de PostgreSQL, ClickHouse
- **Storage**: política de retenção em Bronze (30d) vs Silver (365d)

---

**Última atualização:** 26/02/2024
