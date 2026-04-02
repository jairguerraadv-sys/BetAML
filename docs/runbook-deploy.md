# Runbook de Deploy e Onboarding de Tenants — BetAML

> **Versão**: 2.3 • **Atualizado**: 2026-04-02  
> Destinatários: Engenharia de Plataforma, DevOps, Compliance Ops

---

## Índice

1. [Pré-requisitos](#1-pré-requisitos)  
2. [Primeiro deploy (greenfield)](#2-primeiro-deploy-greenfield)  
3. [Aplicar migrações de banco](#3-aplicar-migrações-de-banco)  
4. [Onboarding de novo tenant (operador de apostas)](#4-onboarding-de-novo-tenant)  
5. [Rollout de nova versão (deploy incremental)](#5-rollout-de-nova-versão)  
6. [Escalamento horizontal](#6-escalamento-horizontal)  
7. [Verificações pós-deploy](#7-verificações-pós-deploy)  
8. [Rollback](#8-rollback)  
9. [Variáveis de ambiente obrigatórias](#9-variáveis-de-ambiente-obrigatórias)  
10. [Checklist rápido](#10-checklist-rápido)

---

## 1. Pré-requisitos

| Ferramenta | Versão mínima | Observação |
|---|---|---|
| Docker | 24.x | Compose v2 incluído |
| kubectl | 1.29+ | Para deploy em k8s |
| Helm | 3.14+ | Chart em `helm/betaml/` |
| PostgreSQL client | 15+ | `psql` para verificações manuais |
| Python | 3.11+ | Para scripts de seed e Alembic |
| pip install alembic psycopg2-binary | — | Na máquina do operador |

```bash
# Verificar versões
docker --version && docker compose version
kubectl version --client
helm version
python3 --version
```

---

## 2. Primeiro deploy (greenfield)

### 2.1 Clonar e configurar variáveis

```bash
git clone git@github.com:jairguerraadv-sys/BetAML.git
cd BetAML

# Copiar template de variáveis
cp infra/.env.example infra/.env   # crie este arquivo se não existir

# Editar OBRIGATORIAMENTE:
#   JWT_SECRET=<min 32 chars aleatórios>
#   PII_ENCRYPTION_KEY=<base64 de 32 bytes>   # gerado abaixo
#   POSTGRES_PASSWORD=<senha forte>
python3 -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

> ⚠️ **Segurança**: `JWT_SECRET` e `PII_ENCRYPTION_KEY` com valores padrão causam
> `RuntimeError` no startup quando `ENVIRONMENT != development`. **Nunca** use os
> valores padrão em staging/produção.

### 2.2 Subir a stack

```bash
cd infra
docker compose up -d postgres redis redpanda minio clickhouse
# Aguardar postgres ficar healthy (≈30s)
docker compose ps

# Subir serviços de aplicação
docker compose up -d api ml_service rules_engine stream_processor frontend
docker compose logs -f api | head -50
```

### 2.3 Verificar health

```bash
curl -sf http://localhost:8000/health/live  && echo "API OK"
curl -sf http://localhost:8000/health/ready && echo "DB OK"
```

---

## 3. Aplicar migrações de banco

### 3.1 Via script operacional (recomendado para produção)

O script `scripts/postgres_migrate_existing.sh` aplica todas as migrations SQL
numeradas de `infra/migration_v*.sql` em ordem, detectando automaticamente quais
já foram aplicadas.

```bash
# Dry-run: mostra o que seria aplicado sem executar
scripts/postgres_migrate_existing.sh --dry-run

# Aplicar todas as migrations pendentes
scripts/postgres_migrate_existing.sh
```

### 3.2 Via Alembic (recomendado para controle de schema em dev/CI)

O Alembic mantém rastreamento de revisões em `alembic_version` no banco.

```bash
cd services/api

# Verificar revisão corrente
DATABASE_URL="postgresql://betaml:devpass@localhost:5432/betaml_dev" \
  alembic current

# Ver histórico de revisões
alembic history --verbose

# Aplicar todas as migrations pendentes
DATABASE_URL="postgresql://betaml:devpass@localhost:5432/betaml_dev" \
  alembic upgrade head

# Gerar nova migration a partir do diff de modelo
DATABASE_URL="..." alembic revision --autogenerate \
  -m "descricao_da_mudanca"
```

> **Convenção de nomeação**: `YYYYMMDD_NNNNNN_descricao_snake_case.py`  
> Exemplo: `20260402_000001_phase3_network_indexes.py`

### 3.3 Stamp em banco existente (sem re-executar migrations já aplicadas)

Se o banco já existe com schema correto mas sem rastreamento Alembic:

```bash
# Marcar como na revisão baseline sem executar nada
DATABASE_URL="..." alembic stamp 20260313_000001

# Depois aplicar só as migrations novas
DATABASE_URL="..." alembic upgrade head
```

### 3.4 Migration v23 — índices CONCURRENTLY

A `migration_v23.sql` usa `CREATE INDEX CONCURRENTLY` que **não pode rodar em
bloco de transação**. Execute diretamente:

```bash
docker compose -f infra/docker-compose.yml exec -T postgres \
  psql -U betaml -d betaml_dev -f /dev/stdin < infra/migration_v23.sql
```

---

## 4. Onboarding de novo tenant

### 4.1 O que é criado automaticamente

Ao iniciar a API com `docker compose up api`, o `seeds.py` verifica se já há
tenants e, se não houver, cria:

- 2 tenants de demonstração (`OperadorA`, `OperadorB`)
- 3 usuários por tenant (admin, analyst, auditor)
- 50 players sintéticos com cenários PLD
- 13 regras DSL default (incluindo `Incompatibilidade renda/volume 30d`)
- 1 `ScoringConfig` por tenant

### 4.2 Criar tenant de produção via API

```bash
# 1. Fazer login como superadmin (tenant bootstrap ou ADMIN global)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_a","password":"admin123"}' | jq -r .access_token)

# 2. Criar tenant
curl -s -X POST http://localhost:8000/admin/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Operador Apostas Ltda",
    "slug": "operador_apostas",
    "cnpj": "00000000000000"
  }' | jq .

# 3. Criar usuário admin do tenant
NEW_TENANT_ID="<id do passo anterior>"
curl -s -X POST http://localhost:8000/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"tenant_id\": \"$NEW_TENANT_ID\",
    \"username\": \"admin_operador\",
    \"email\": \"admin@operadorapostas.com.br\",
    \"password\": \"senhaSegura123!\",
    \"role\": \"ADMIN\"
  }" | jq .
```

### 4.3 Configurar ScoringConfig para o tenant

```bash
curl -s -X PUT "http://localhost:8000/admin/tenants/$NEW_TENANT_ID/scoring-config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "rule_weight": 0.40,
    "ml_weight": 0.40,
    "network_weight": 0.20,
    "auto_case_threshold": 0.75,
    "income_volume_ratio_threshold": 3.0,
    "sla_critical_hours": 4,
    "sla_high_hours": 24,
    "sla_medium_hours": 72
  }' | jq .
```

### 4.4 Importar lista de players (CSV)

```bash
# Formato: external_player_id,cpf,name,birth_date,declared_income_monthly
curl -s -X POST "http://localhost:8000/ingest/file" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@players.csv" \
  -F "source_system=BACKOFFICE" \
  -F "entity_type=PLAYER" | jq .
```

### 4.5 Criar API Key para ingestão automatizada

```bash
curl -s -X POST "http://localhost:8000/admin/api-keys" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Connector Producao",
    "source_system": "BACKOFFICE_ALPHA",
    "permissions": ["ingest"]
  }' | jq .
# Salve o campo "key" retornado — não será exibido novamente
```

---

## 5. Rollout de nova versão

### 5.1 Deploy zero-downtime com Helm

```bash
cd helm/betaml

# Atualizar values com nova tag de imagem
helm upgrade betaml . \
  --namespace betaml \
  --set api.image.tag=v2.4.0 \
  --set mlService.image.tag=v2.4.0 \
  --atomic \           # rollback automático se falhar
  --timeout 10m \
  --wait

# Verificar rollout
kubectl rollout status deployment/betaml-api -n betaml
```

### 5.2 Deploy com docker compose (staging/single-node)

```bash
# Build da nova imagem
docker compose -f infra/docker-compose.yml build api ml_service

# Rolling restart sem downtime (Compose v2.20+)
docker compose -f infra/docker-compose.yml up -d --no-deps api ml_service

# Verificar
docker compose logs api --tail=50
curl -sf http://localhost:8000/health/ready
```

### 5.3 Aplicar migrations antes do restart

```bash
# 1. Aplicar SQL concurrently (índices — sem lock)
scripts/postgres_migrate_existing.sh

# 2. Aplicar Alembic (alterações de schema)
cd services/api
DATABASE_URL="$PROD_DATABASE_URL" alembic upgrade head

# 3. Reiniciar serviços
docker compose up -d --no-deps api
```

---

## 6. Escalamento horizontal

### 6.1 Múltiplas réplicas da API

```bash
# docker compose
docker compose -f infra/docker-compose.yml up -d --scale api=3

# Helm
helm upgrade betaml helm/betaml/ --set api.replicaCount=3
```

> **Pré-requisito**: variável `REDIS_URL` configurada para instância compartilhada.
> O rate limiter (slowapi) e o online feature store usam Redis — sem Redis compartilhado,
> réplicas terão estados independentes.

### 6.2 ML Trainer (single instance)

O `ml_trainer` deve rodar como **single replica** — o scheduler APScheduler não
é distribuído. Use `maxReplicas=1` no HPA ou `replicas=1` no Helm:

```bash
helm upgrade betaml helm/betaml/ --set mlTrainer.replicaCount=1
```

### 6.3 Stream processor (particionamento Kafka)

```bash
# Aumentar partições do tópico antes de escalar
docker compose exec redpanda rpk topic add-partitions betaml-events --num 6

# Escalar consumidores
docker compose up -d --scale stream_processor=3
```

---

## 7. Verificações pós-deploy

```bash
# Health completo
curl -sf http://localhost:8000/health/live   && echo "live OK"
curl -sf http://localhost:8000/health/ready  && echo "ready OK"

# Dashboard PLD KPIs (autenticado)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -d '{"username":"analyst_a","password":"analyst123"}' \
  -H "Content-Type: application/json" | jq -r .access_token)

curl -sf -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/stats/pld-kpis | jq .coaf_funnel

# Qualidade de dados
curl -sf -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/stats/data-quality | jq .overall_status

# Sanctions checker carregado
curl -sf -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/sanctions/status | jq .

# Modelo ML ativo
curl -sf -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/ml/models?status=champion | jq length

# Verificar versão do schema Alembic
cd services/api
DATABASE_URL="$DATABASE_URL" alembic current
```

---

## 8. Rollback

### 8.1 Rollback Alembic (schema)

```bash
# Ver revisões disponíveis
cd services/api && alembic history

# Desfazer última migration
DATABASE_URL="$PROD_DATABASE_URL" alembic downgrade -1

# Desfazer até revisão específica
DATABASE_URL="$PROD_DATABASE_URL" alembic downgrade 20260320_000001
```

> ⚠️ **Indices CONCURRENTLY** (`migration_v23.sql`) devem ser removidos manualmente:
> ```sql
> DROP INDEX CONCURRENTLY IF EXISTS idx_device_events_device_hash;
> -- (repetir para cada índice)
> ```

### 8.2 Rollback Helm

```bash
helm rollback betaml --namespace betaml
# ou para revisão específica:
helm history betaml -n betaml
helm rollback betaml <revision_number> -n betaml
```

### 8.3 Rollback docker compose

```bash
# Voltar para imagem anterior (tag específica)
docker compose -f infra/docker-compose.yml up -d --no-deps \
  -e API_IMAGE_TAG=v2.3.0 api
```

---

## 9. Variáveis de ambiente obrigatórias

| Variável | Descrição | Padrão dev | Produção |
|---|---|---|---|
| `DATABASE_URL` | PostgreSQL asyncpg URL | `postgresql+asyncpg://...` | **Obrigatório** |
| `JWT_SECRET` | Segredo para assinar JWT | `dev-secret-change-me` | **Min 32 chars** |
| `PII_ENCRYPTION_KEY` | Chave Fernet base64-32b | valor dev | **Rotacionar anualmente** |
| `REDIS_URL` | Redis para rate limit e cache | `redis://localhost:6379` | Redis Sentinel/Cluster |
| `MINIO_ENDPOINT` | Endpoint S3-compatible | `http://minio:9000` | S3 real ou MinIO HA |
| `MINIO_ACCESS_KEY` | Access key MinIO/S3 | `minio` | IAM role preferencial |
| `MINIO_SECRET_KEY` | Secret key MinIO/S3 | `minio123` | IAM role preferencial |
| `CLICKHOUSE_HOST` | Host ClickHouse analytics | `clickhouse` | **Obrigatório** |
| `SANCTIONS_CSV_PATH` | CSV de sanções/PEP | `/data/sanctions.csv` | `/etc/betaml/sanctions.csv` |
| `ENVIRONMENT` | `development`/`staging`/`production` | `development` | `production` |
| `EXTERNAL_VALIDATION_PROVIDER` | Provider KYC externo | `mock` | nome do provider real |
| `CORS_ALLOW_ORIGINS` | Origens CORS permitidas | `*` | domínio específico |

---

## 10. Checklist rápido

### Deploy de nova versão

- [ ] `git pull` + verificar CHANGELOG.md
- [ ] `scripts/postgres_migrate_existing.sh --dry-run` — revisar SQL
- [ ] `alembic upgrade head` em staging primeiro
- [ ] Testes de smoke: `pytest tests/unit -x -q`
- [ ] Deploy em staging → verificar `GET /health/ready`
- [ ] Validar KPIs: `GET /stats/pld-kpis` retorna 200
- [ ] Deploy em produção com `--atomic`
- [ ] `GET /stats/data-quality` → `overall_status` = "OK"
- [ ] Notificar time de compliance pós-deploy

### Onboarding de tenant

- [ ] Criar tenant via `POST /admin/tenants`
- [ ] Criar usuário ADMIN do tenant
- [ ] Configurar `ScoringConfig` (`PUT /admin/tenants/{id}/scoring-config`)
- [ ] Gerar API Key para conector de ingestão
- [ ] Importar players históricos (CSV)
- [ ] Validar: `GET /stats/dashboard` retorna dados do novo tenant
- [ ] Executar seed de regras DSL: verificar 13 regras ativas
- [ ] Confirmar que modelo ML está ativo: `GET /ml/models?status=champion` ≥ 1
- [ ] Testar ingestão de evento de teste via `POST /ingest/event`
- [ ] Entregar credenciais ao operador com protocolo de rotação de senha
