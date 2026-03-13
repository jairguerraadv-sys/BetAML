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

Execute as migrations em ordem crescente de número. O arquivo `init-db.sql` cria o schema base (v1). As migrations incrementais adicionam tabelas e colunas conforme o projeto evolui.

```bash
# Helper: aplica uma migration específica
apply_migration() {
  local version=$1
  docker compose exec postgres psql -U betaml -d betaml \
    -f /migrations/migration_v${version}.sql
}

# Aplicar sequencialmente a partir de v2
for v in 2 3 4 5 6 7 8 9 10 11 12; do
  echo "=== Aplicando migration v$v ==="
  apply_migration $v
done
```

### Resumo de Cada Migration

| Versão | Descrição |
|--------|-----------|
| v2 | Tabelas secundárias: `system_flags`, `notifications`, `feature_snapshots`, `scoring_configs`, `player_lists`, `rule_macros`, `api_keys`, `compound_rules`, `ingest_errors`; coluna `weight` em `rules` |
| v3 | Tabelas OLTP: `financial_transactions`, `bets`, `device_events`; RLS por tenant |
| v4 | Extensão `pgcrypto`; colunas `status`/`analyst_narrative`/`pdf_url` em `report_packages`; coluna `pii_accessed` em `audit_logs` |
| v5 | Colunas em `compound_rules`, `model_registry`, `player_list_entries`; `risk_band` em `players`; `auto_created` em `cases`; thresholds em `scoring_configs`; índices adicionais |
| v6 | Colunas de threshold (`low_threshold` … `critical_threshold`), `is_active` e `data_retention_days` em `scoring_configs` |
| v7 | Coluna `snapshot_date` em `feature_snapshots`; índice por tenant/player/snapshot_date |
| v8 | Coluna `is_read` em `notifications`; backfill de legado `read → is_read`; default `false` |
| v9 | Colunas `reference_type`/`reference_id` em `notifications`; constraint `chk_player_status` em `players` (inclui `ERASED`); índice filtrado `status != 'ERASED'` |
| v10 | Coluna `feature_version INTEGER NOT NULL DEFAULT 2` em `feature_snapshots`; índice por tenant/player/feature_version |
| v11 | Índices de performance em queries de alta frequência: alerts, cases, players, transactions, audit_logs, etc. (30+ índices) |
| v12 | Coluna `label_note TEXT` em `alerts` — nota de investigação do analista ao rotular alertas (LGPD conformidade + feedback loop) |
| v13 | Coluna `cnpj VARCHAR(14)` em `tenants` (COAF MIFD v3 obrigatório); coluna `pii_accessed TEXT` em `audit_logs` + índice para rastreabilidade LGPD Art. 37 |

### Aplicar Migration Individual

```bash
# Exemplo: aplicar apenas a v9
docker compose exec postgres psql -U betaml -d betaml \
  -f /migrations/migration_v9.sql
```

### Verificar Migrações Aplicadas

```bash
# Checar se coluna snapshot_date existe (v7)
docker compose exec postgres psql -U betaml -d betaml -c \
  "SELECT column_name FROM information_schema.columns
   WHERE table_name='feature_snapshots' AND column_name='snapshot_date';"

# Checar constraint de status (v9)
docker compose exec postgres psql -U betaml -d betaml -c \
  "\d players" | grep chk_player_status

# Checar coluna feature_version (v10)
docker compose exec postgres psql -U betaml -d betaml -c \
  "SELECT column_name, data_type, column_default
   FROM information_schema.columns
   WHERE table_name='feature_snapshots' AND column_name='feature_version';"

# Checar índices de performance criados na v11
docker compose exec postgres psql -U betaml -d betaml -c \
  "SELECT indexname FROM pg_indexes WHERE schemaname='public'
   AND indexname LIKE 'idx_%' ORDER BY indexname;" | wc -l
# Deve retornar ≥ 30 índices

# Checar coluna label_note em alerts (v12)
docker compose exec postgres psql -U betaml -d betaml -c \
  "SELECT column_name FROM information_schema.columns
   WHERE table_name='alerts' AND column_name='label_note';"
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

## 11. Feature Store Operacional

### Endpoints Canônicos

Use estes endpoints como contrato principal do feature store:

```bash
# Features atuais do player (Redis online store)
GET /feature-store/players/{player_id}/current

# Histórico de snapshots (Postgres/Gold)
GET /feature-store/players/{player_id}/history?from=2026-03-01T00:00:00Z&to=2026-03-10T23:59:59Z
```

### Endpoints Legados Compatíveis

Os endpoints abaixo continuam ativos por compatibilidade e retornam payload equivalente quando aplicável:

```bash
GET /players/{player_id}/features/current
GET /players/{player_id}/features
GET /players/{player_id}/feature-history?days=30
```

### Observações Operacionais

- O endpoint current normaliza tipos vindos do Redis antes de responder, preservando `bool`, `int` e `float`.
- O histórico canônico retorna `items[]` com `snapshot_date`, `created_at`, `features` e `drift_score`.
- A rota legada `feature-history` expõe aliases compatíveis como `unique_instruments_used_7d` e `bonus_to_real_money_ratio_30d`.

## 12. Troubleshooting

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

## 13. Verificações de Isolamento Multi-tenant (Checklist)

Use dois tokens de tenants distintos (`$TOKEN_A` e `$TOKEN_B`) para validar
que recursos de um tenant não são acessíveis pelo outro.

### 13.1 Audit log (novo e legado)

```bash
curl -i -s http://localhost:8000/audit-logs -H "Authorization: Bearer $TOKEN_A"
curl -i -s http://localhost:8000/audit-log  -H "Authorization: Bearer $TOKEN_A"
```

Sem token, ambos devem retornar `401`/`403`.

### 13.2 Ingest job por tenant

```bash
curl -i -s http://localhost:8000/ingest/jobs/$JOB_ID_A -H "Authorization: Bearer $TOKEN_B"
```

Esperado: `403` ou `404` para acesso cross-tenant.

### 13.3 Reprocessamento cross-tenant bloqueado

```bash
curl -i -s -X POST "http://localhost:8000/ingest/jobs/$JOB_ID_A/reprocess" \
  -H "Authorization: Bearer $TOKEN_B" \
  -H "Content-Type: application/json" \
  -d '{"reason":"cross-tenant attempt"}'
```

Esperado: `403` ou `404`.

### 13.4 Erros de ingestão cross-tenant

```bash
curl -i -s "http://localhost:8000/ingest/errors?job_id=$JOB_ID_A" \
  -H "Authorization: Bearer $TOKEN_B"
```

Esperado: lista vazia para listagem e `403`/`404` para tentativa de resolve em erro de outro tenant.
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

---

## 14. Procedure de Rotação de Chaves Criptográficas

> **Criticalidade: ALTA.** Execute este procedure em manutenção programada com comunicação prévia
> aos usuários, pois todos os tokens JWT ativos serão invalidados durante o processo.

### 14.1 Rotação do JWT_SECRET

A rotação do `JWT_SECRET` invalida **todos os tokens JWT ativos** no momento da troca.
Os usuários precisarão re-autenticar após o restart.

```bash
# 1. Gerar novo segredo (mínimo 32 bytes)
NEW_JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
echo "Novo JWT_SECRET: $NEW_JWT_SECRET"

# 2. Atualizar .env
sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$NEW_JWT_SECRET/" .env

# 3. Invalidar blacklist Redis (tokens antigos são inválidos de qualquer forma pós-restart)
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" FLUSHDB

# 4. Restart da API (invalida todos os tokens em circulação)
docker compose restart api

# 5. Verificar que a API subiu com novo secret
curl http://localhost:8000/health
```

### 14.2 Rotação do PII_ENCRYPTION_KEY (chave Fernet de CPF)

> **ATENÇÃO CRÍTICA:** A rotação da `PII_ENCRYPTION_KEY` **RE-ENCRIPTA todos os CPFs** no banco.
> Se executado parcialmente (ex: crash no meio), parte dos registros ficará com a chave nova
> e parte com a antiga. Execute **sempre** com backup completo e em transação.

```bash
# 1. BACKUP OBRIGATÓRIO antes de qualquer rotação de PII_ENCRYPTION_KEY
docker compose exec postgres pg_dump -U betaml betaml_dev > backup_pre_rotation_$(date +%Y%m%d_%H%M%S).sql

# 2. Gerar nova chave Fernet (base64-urlsafe, 32 bytes)
NEW_PII_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
echo "Nova PII_ENCRYPTION_KEY: $NEW_PII_KEY"

# 3. Executar script de re-encriptação (requer ambas as chaves)
# O script lê com a chave ANTIGA e grava com a chave NOVA
docker compose exec api python - <<'EOF'
import os, asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

OLD_KEY = os.environ["PII_ENCRYPTION_KEY"].encode()
NEW_KEY = input("Digite a NOVA PII_ENCRYPTION_KEY: ").strip().encode()

old_fernet = Fernet(OLD_KEY)
new_fernet = Fernet(NEW_KEY)

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(DATABASE_URL, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)

async def reencrypt():
    from models import Player
    async with Session() as db:
        players = (await db.execute(select(Player))).scalars().all()
        for p in players:
            if p.cpf_encrypted and not p.cpf_encrypted.startswith(b"ERASURE_"):
                plain = old_fernet.decrypt(p.cpf_encrypted)
                p.cpf_encrypted = new_fernet.encrypt(plain)
            if p.name_encrypted and not p.name_encrypted.startswith(b"ERASURE_"):
                plain = old_fernet.decrypt(p.name_encrypted)
                p.name_encrypted = new_fernet.encrypt(plain)
        await db.commit()
        print(f"Re-encriptados: {len(players)} players")

asyncio.run(reencrypt())
EOF

# 4. Atualizar .env com a nova chave
sed -i "s|^PII_ENCRYPTION_KEY=.*|PII_ENCRYPTION_KEY=$NEW_PII_KEY|" .env

# 5. Restart da API com a nova chave
docker compose restart api

# 6. Verificar que a API descifra CPFs corretamente
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123"}' | jq .access_token
```

### 14.3 Rotação de Redis Password

```bash
# 1. Gerar nova senha
NEW_REDIS_PW=$(python -c "import secrets; print(secrets.token_hex(16))")

# 2. Atualizar .env
sed -i "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$NEW_REDIS_PW/" .env

# 3. Atualizar REDIS_URL no .env
sed -i "s|redis://:[^@]*@|redis://:$NEW_REDIS_PW@|g" .env

# 4. Restart Redis + serviços dependentes
docker compose restart redis api stream-processor rules-engine ml-service
```

### 14.4 Checklist pós-rotação

Após qualquer rotação de chave, verificar:

```bash
# API saudável
curl http://localhost:8000/health

# Login funcional com novo JWT_SECRET
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123"}' | jq '.access_token | length'

# CPF descifrado corretamente (deve mostrar CPF mascarado, não erro)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin_a", "password": "admin123"}' | jq -r .access_token)

curl -s http://localhost:8000/players?limit=1 \
  -H "Authorization: Bearer $TOKEN" | jq '.[0].cpf_masked'

# Audit log registra a rotação
echo "Registre manualmente no audit_log: ação=ROTATE_SECRET, entity=API, motivo=rotação programada"
```

### 14.5 Frequência recomendada

| Chave               | Frequência mínima | Gatilho adicional                        |
|---------------------|-------------------|------------------------------------------|
| `JWT_SECRET`        | 90 dias           | Suspeita de comprometimento, saída de dev |
| `PII_ENCRYPTION_KEY`| 180 dias          | Suspeita de acesso não autorizado ao DB   |
| `REDIS_PASSWORD`    | 90 dias           | Saída de membro da equipe ops             |
| API Keys (`btml_*`) | 365 dias          | Saída de parceiro/integração              |
