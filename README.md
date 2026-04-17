# BetAML

Plataforma multi-tenant de PLD/FT para operadores de apostas online (esportivas, casino ao vivo, slots, jogos instantâneos, bingo e raspadinha digital), com ingestao industrializada, feature store online/offline, motor de regras, ML, case management, governanca, observabilidade e frontend operacional. Conforme Lei 14.790/2023 art. 3º e Portarias SPA/MF 1.143/2024 e 1.231/2024.

## Stack

- `services/api`: FastAPI, auth JWT, RBAC, ingest, rules, cases, reports, admin e OpenAPI
- `services/stream_processor`: consumidor Kafka/Redpanda para features online/offline e jobs de qualidade
- `services/rules_engine`: avaliacao da DSL, listas, compound rules e scoring auditavel
- `services/ml_service`: inferencia, explicabilidade e champion/challenger
- `services/ml_trainer`: retreino supervisionado e nao supervisionado
- `services/frontend`: Next.js 14 com dashboard, investigacao, admin e onboarding
- `infra`: `docker-compose`, Prometheus, Grafana, Redpanda, Postgres, Redis, MinIO e ClickHouse
- `libs`: schemas, parser DSL, mapeamentos, clientes compartilhados e telemetry helpers

## Arquitetura

```text
Frontend (Next.js)
        |
        v
API (FastAPI) --------------------> PostgreSQL
   |   |   \                       Redis
   |   |    \                      MinIO
   |   |     \                     ClickHouse
   |   |      \
   |   +-------> ML Service
   |
   +-------> Redpanda / Kafka --------> Stream Processor
                     \
                      +---------------> Rules Engine
```

## Subir a stack completa

Pre-requisitos:

- Docker Engine 25+
- Docker Compose v2
- 8 GB de RAM livres recomendados

Comandos:

```bash
docker compose -f infra/docker-compose.yml up -d --build
curl http://localhost:8000/health
```

Servicos locais:

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- OpenAPI JSON live: `http://localhost:8000/openapi.json`
- Frontend: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`
- MinIO Console: `http://localhost:9001`
- Redpanda Console: `http://localhost:8080`

## Configuracao principal

Defaults de desenvolvimento suportados pelo projeto:

```env
PROJECT_NAME=betaml
JWT_SECRET=dev-secret-change-me
POSTGRES_PASSWORD=devpass
REDIS_PASSWORD=devpass
MINIO_ACCESS_KEY=minio
MINIO_SECRET_KEY=minio123
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
PROMETHEUS_PORT=9090
GRAFANA_PORT=3001
```

Variaveis adicionais importantes:

- `EPSILON_WEBHOOK_SECRET`: segredo HMAC do `ConnectorEpsilon`
- `PII_ENCRYPTION_KEY`: cifragem de PII
- `NEXT_PUBLIC_API_URL`: URL publica da API para o frontend
- `BACKEND_API_URL`: URL interna usada pelas routes server-side do frontend

## Seed e login inicial

```bash
cd services/api
python seeds.py
```

Login:

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_a","password":"admin123","tenant_slug":"operador_a"}'
```

Operacoes de plataforma, como onboarding de tenant, usam o principal bootstrapado `superadmin` / `superadmin123` por padrao local (ou os overrides `SUPER_ADMIN_USER` / `SUPER_ADMIN_PASS`).

## Fluxos principais

Ingestao:

```bash
curl -s -X POST http://localhost:8000/ingest/file \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@transactions.csv;type=text/csv" \
  -F "source_system=BackofficeAlpha" \
  -F "entity_type=transaction"
```

Simulacao de regra:

```bash
curl -s -X POST http://localhost:8000/rules/<rule-id>/simulate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date_from":"2026-03-01T00:00:00Z","date_to":"2026-03-20T23:59:59Z"}'
```

Retreino e promocao de modelo:

```bash
curl -s -X POST http://localhost:8001/train/structuring
curl -s -X POST http://localhost:8000/model-registry/<model-id>/promote \
  -H "Authorization: Bearer $TOKEN"
```

Geracao de `ReportPackage`:

```bash
curl -s -X POST http://localhost:8000/cases/<case-id>/report-package \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"FILE_SAR","analyst_narrative":"Narrativa do caso"}'
```

## Testes

Python:

```bash
DEBUG=false bash scripts/run_critical_unit_batches.sh --include-remainder -q --tb=short \
  --cov=services/api \
  --cov-report=term-missing \
  --cov-report=xml:coverage.xml \
  --cov-fail-under=40
```

Suite focada apenas nos modulos criticos:

```bash
DEBUG=false bash scripts/run_critical_unit_batches.sh -q --tb=short \
  --cov=services/api \
  --cov-fail-under=40
```

Frontend:

```bash
services/frontend/node_modules/.bin/tsc -p services/frontend/tsconfig.json --noEmit
```

Carga:

```bash
locust -f tests/load/locustfile.py --host http://localhost:8000
python tests/load/generate_report.py /tmp/betaml_load_results
```

## Documentacao

Guias principais:

- [README de E2E Playwright](/workspaces/BetAML/e2e/README.md)
- [README da API](/workspaces/BetAML/services/api/README.md)
- [README do Frontend](/workspaces/BetAML/services/frontend/README.md)
- [README do Stream Processor](/workspaces/BetAML/services/stream_processor/README.md)
- [README do Rules Engine](/workspaces/BetAML/services/rules_engine/README.md)
- [README do ML Service](/workspaces/BetAML/services/ml_service/README.md)
- [README do ML Trainer](/workspaces/BetAML/services/ml_trainer/README.md)
- [Guia de Operacoes](/workspaces/BetAML/docs/ops-guide.md)
- [Guia do Analista PLD](/workspaces/BetAML/docs/analyst-guide.md)
- [Guia de Contribuicao](/workspaces/BetAML/docs/contributing.md)
- [OpenAPI por tags](/workspaces/BetAML/docs/openapi-tags.md)
- [OpenAPI estatico JSON](/workspaces/BetAML/docs/openapi.json)

## OpenAPI

O contrato da API pode ser consumido de tres formas:

- live: `GET /openapi.json`
- UI: `GET /docs` e `GET /redoc`
- snapshot versionado no repositorio: [`docs/openapi.json`](/workspaces/BetAML/docs/openapi.json)

Para regenerar o snapshot:

```bash
python scripts/export_openapi.py
```

## Operacao rapida

- Backfill historico: veja [Guia de Operacoes](/workspaces/BetAML/docs/ops-guide.md)
- Reprocessamento de ingest job: veja [Guia de Operacoes](/workspaces/BetAML/docs/ops-guide.md)
- Rollback de MappingConfig: veja [Guia de Operacoes](/workspaces/BetAML/docs/ops-guide.md)
- Investigacao e reporte: veja [Guia do Analista PLD](/workspaces/BetAML/docs/analyst-guide.md)

## Seeds e cenarios

Os seeds cobrem cenarios suspeitos de:

- structuring
- spike transacional
- rede compartilhada por device e instrumento
- round-tripping
- PEP

Base principal: `services/api/seeds.py`.
