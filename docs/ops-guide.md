# BetAML — Guia de Operações (Ops Guide)

## 1. Visão Geral da Arquitetura

```
Internet
   │
   ▼
Next.js Frontend  (port 3000)
   │
   ▼
FastAPI API        (port 8000)  ←→  PostgreSQL 16   (port 5432)
   │                            ←→  Redis 7          (port 6379)
   │                            ←→  MinIO            (port 9000)
   │
   ▼
Redpanda (Kafka)   (port 9092)
   │
   ├─▶ Stream Processor  (internal)
   ├─▶ Rules Engine      (internal)
   └─▶ ML Service        (port 8001)
        │
        └─▶ ClickHouse    (port 8123)

Observability:
  Prometheus  (port 9090)
  Grafana     (port 3001)
```

## 2. Pré-requisitos

| Ferramenta   | Versão mínima |
|-------------|---------------|
| Docker Engine | 25.x         |
| Docker Compose | v2.24         |
| RAM disponível | 8 GB         |
| Disco livre   | 20 GB         |

## 3. Inicialização do Ambiente

### 3.1 Clone e Subida Completa

```bash
git clone https://github.com/jairguerraadv-sys/BetAML.git
cd BetAML/infra

# Primeira vez — constrói imagens e inicializa banco
docker compose up -d --build

# Aguardar serviços (≈90s)
docker compose ps
```

### 3.2 Verificação de Saúde

```bash
# API
curl http://localhost:8000/health

# ML Service
curl http://localhost:8001/health

# Prometheus
curl http://localhost:9090/-/healthy

# Grafana
open http://localhost:3001   # admin / admin123
```

### 3.3 Criação do Primeiro Usuário Admin

```bash
docker compose exec api python -c "
from asyncio import run
from database import AsyncSessionLocal
from models import User
from auth import hash_password
import uuid

async def create():
    async with AsyncSessionLocal() as db:
        u = User(
            id=str(uuid.uuid4()),
            email='admin@betaml.io',
            username='admin',
            password_hash=hash_password('Admin@123'),
            role='ADMIN',
            tenant_id='default',
        )
        db.add(u)
        await db.commit()
        print('Usuário criado:', u.email)
run(create())
"
```

## 4. Migrações de Banco de Dados

### Ordem de Execução

```bash
# Primeiro: schema base (criado automaticamente no init-db.sql)
# Após primeira subida, aplicar migration v2:
docker compose exec postgres psql -U betaml -d betaml -f /docker-entrypoint-initdb.d/migration_v2.sql
```

### Reverter Migration v2

```sql
-- Execute manualmente se necessário:
DROP TABLE IF EXISTS system_flags, notifications, feature_snapshots,
  scoring_configs, player_list_entries, player_lists, compound_rules,
  rule_macros, api_keys, ingest_errors CASCADE;
-- Remover colunas adicionadas:
ALTER TABLE rules DROP COLUMN IF EXISTS weight;
ALTER TABLE mapping_config_versions DROP COLUMN IF EXISTS is_current;
-- etc.
```

## 5. Atualização de Serviços

```bash
# Atualizar só a API sem downtime:
docker compose up -d --no-deps --build api

# Atualizar ML Service:
docker compose up -d --no-deps --build ml_service

# Rebuild completo:
docker compose down
docker compose up -d --build
```

## 6. Observabilidade

### Prometheus

Acesse `http://localhost:9090`. Principais queries:

```promql
# Taxa de requisições da API (req/s, 1m)
sum(rate(http_requests_total{job="betaml-api"}[1m]))

# Latência p99 da API (ms)
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job="betaml-api"}[5m])) by (le)) * 1000

# Taxa de erros 5xx (%)
sum(rate(http_requests_total{job="betaml-api",status=~"5.."}[5m])) /
sum(rate(http_requests_total{job="betaml-api"}[5m])) * 100

# Latência p99 do ML Service (ms)
histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job="betaml-ml-service"}[5m])) by (le)) * 1000
```

### Grafana

Dashboard pré-provisionado: **BetAML — Platform Overview**

- URL: `http://localhost:3001/d/betaml-overview`
- Usuário: `admin` / Senha: `admin123`
- Painéis: API req/s, latência p50/p95/p99, taxa de erros, eventos Redpanda, latência ML

### Logs Estruturados

Todos os serviços emitem JSON via `structlog`:

```bash
# Tail logs da API
docker compose logs -f --tail=100 api | jq .

# Filtrar erros
docker compose logs api | grep '"level":"error"' | jq .
```

## 7. Backup e Recuperação

### PostgreSQL

```bash
# Backup
docker compose exec postgres pg_dump -U betaml betaml | gzip > backup_$(date +%Y%m%d).sql.gz

# Restore
gunzip -c backup_20241201.sql.gz | docker compose exec -T postgres psql -U betaml betaml
```

### MinIO (modelos ML e evidências)

```bash
# Via mc (MinIO Client)
docker run --rm --network betaml-net \
  minio/mc mirror betaml/betaml-models /backup/models
```

### Redis (features em memória)

Redis é cache volátil — não requer backup. TTL padrão: 4 horas.

## 8. Configuração de Variáveis de Ambiente

| Variável | Serviço | Default | Descrição |
|----------|---------|---------|-----------|
| `DATABASE_URL` | api | `postgresql+asyncpg://betaml:betaml@postgres/betaml` | DSN PostgreSQL |
| `REDIS_URL` | api, stream_processor | `redis://redis:6379` | URL Redis |
| `KAFKA_BOOTSTRAP` | todos | `redpanda:9092` | Broker Kafka/Redpanda |
| `MINIO_ENDPOINT` | api, ml_service | `minio:9000` | Endpoint MinIO |
| `MINIO_ACCESS_KEY` | api, ml_service | `betaml` | Chave de acesso MinIO |
| `MINIO_SECRET_KEY` | api, ml_service | `betaml123` | Chave secreta MinIO |
| `SECRET_KEY` | api | `change-me-in-prod-256bit` | Segredo JWT |
| `CLICKHOUSE_HOST` | stream_processor, ml_service | `clickhouse` | Host ClickHouse |
| `ML_SERVICE_URL` | rules_engine | `http://ml_service:8001` | URL ML Service |
| `MAINTENANCE_MODE` | api | `false` | Bloqueia ingestão |

## 9. Escalonamento

### Horizontal (múltiplas réplicas)

```yaml
# docker-compose.override.yml
services:
  api:
    deploy:
      replicas: 3
  stream_processor:
    deploy:
      replicas: 2
```

### Vertical (aumento de recursos)

```yaml
services:
  ml_service:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
```

## 10. SLAs e Alertas Recomendados

| Métrica | Alerta (warning) | Alerta (critical) |
|---------|-----------------|-------------------|
| API p99 latência | > 200 ms | > 1000 ms |
| API taxa de erros 5xx | > 1% | > 5% |
| ML scoring p99 | > 500 ms | > 2000 ms |
| Lag Redpanda | > 10.000 | > 100.000 |
| Disco PostgreSQL | > 70% | > 90% |

Configure alertas no Grafana em: **Alerting → Alert rules → New alert rule**.

## 11. Troubleshooting

### API não responde

```bash
docker compose ps api        # checar estado
docker compose logs --tail=50 api
docker compose restart api

## 12. Runbook de Ingestão (DLQ, Erros e Reprocessamento)

### 12.1 Consultar erros de ingestão

```bash
curl -s "http://localhost:8000/ingest/errors?limit=50" \
  -H "Authorization: Bearer $TOKEN"
```

Filtros suportados:

- `job_id`
- `resolved` (`true` ou `false`)
- `source_system`
- `limit` / `offset`

### 12.2 Marcar erro como resolvido

```bash
curl -s -X POST "http://localhost:8000/ingest/errors/$ERROR_ID/resolve" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"note":"corrigido em backoffice"}'
```

### 12.3 Reprocessar job com arquivo Bronze

```bash
curl -s -X POST "http://localhost:8000/ingest/jobs/$JOB_ID/reprocess" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"reprocess after mapping fix"}'
```

Comportamento operacional:

- Reprocessamento exige Kafka disponível.
- Reprocessamento exige `file_path` (arquivo Bronze) no job original.
- Em falha de enqueue após retries, o novo job é marcado como `FAILED`.

### 12.4 Parse dedicado de conectores (Gamma/Delta)

```bash
curl -s -X POST "http://localhost:8000/ingest/connectors/gamma/parse" \
  -H "Authorization: Bearer $TOKEN" \
  -F "entity_type=transaction" \
  -F "file=@./sample-gamma.xml;type=application/xml"
```

```bash
curl -s -X POST "http://localhost:8000/ingest/connectors/delta/parse" \
  -H "Authorization: Bearer $TOKEN" \
  -F "entity_type=transaction" \
  -F "file=@./sample-delta.ndjson;type=application/x-ndjson"
```

Resposta inclui:

- `job_id`
- `source_system`
- `status` (`DONE`, `PARTIAL` ou `FAILED`)
- `summary.accepted`, `summary.failed`, `summary.total`, `summary.errors`
```

### ML Service fora do ar / modelos não carregados

```bash
docker compose logs ml_service | grep "error\|model"
# Se modelo não existir no MinIO, treinar:
curl -X POST http://localhost:8001/train \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"default"}'
```

### Redpanda lag crescente

```bash
docker compose exec redpanda rpk topic list
docker compose exec redpanda rpk group describe betaml-stream-processor
```

### ClickHouse sem dados

```bash
docker compose exec clickhouse clickhouse-client \
  --query "SELECT count() FROM betaml.player_features_daily"
```
