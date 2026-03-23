# BetAML - Go-Live Checklist

Fonte canonica de auditoria e readiness:
- docs/auditoria-consolidada-pld-2026-03-20.md

## 1. Mudancas e release

- Versao atualizada no `CHANGELOG.md` com escopo e rollback plan.
- PR aprovado por backend, frontend e compliance.
- Commits de release etiquetados (`git tag`).
- Checklist de seguranca concluido (secrets, permissoes, audit trail).

## 2. Banco e migracoes

- Backup logico criado antes do deploy.
- `scripts/postgres_migrate_existing.sh --dry-run` executado sem erro.
- Alembic baseline conferido (`alembic -c services/api/alembic.ini heads`).
- Se ambiente legado: baseline marcado com `stamp 20260313_000001`.
- Rollback SQL testado em ambiente de homologacao.

## 3. Configuracoes e segredos

- Secrets de producao validados sem defaults.
- Variaveis obrigatorias revisadas: `JWT_SECRET`, `PII_ENCRYPTION_KEY`, `BACKEND_API_URL`, `NEXT_PUBLIC_API_URL`.
- E2E CI configurado com `vars.E2E_USERNAME` e `secrets.E2E_PASSWORD`.

## 4. Validacao funcional

- API health: `GET /health`.
- Frontend login + rotas protegidas.
- Fluxo de alertas: listar, abrir detalhe, triagem e vinculação a caso.
- Fluxo de casos: criar caso, abrir detalhe, comentar, mudar status e gerar dossiê.
- Ingestao smoke: `raw.* -> canonical.* -> features.*` observada em logs.
- Gate Python alinhado ao CI: `DEBUG=false bash scripts/run_critical_unit_batches.sh --include-remainder -q --tb=short --cov=services/api --cov-fail-under=40`.
- E2E (pipeline + ML): `TEST_STACK_UP=1 pytest tests/integration/ -v` executado em staging.

## 5. Observabilidade e operacao

- Dashboards Grafana revisados (API latency, DLQ, ingest throughput).
- Alertas ativos: erros de migracao, backlog de fila, 5xx API.
- On-call informado com janela de deploy e rollback procedure.

## 5.1 Backups

- Backup diario habilitado (Helm CronJob) ou rotina equivalente em producao.
- Teste de restore (Postgres + artefatos MinIO) executado em ambiente de homologacao.

## 6. Pos-deploy (primeiros 60 minutos)

- Conferir taxa de erro 5xx < 1%.
- Conferir latencia p95 da API em nivel esperado.
- Confirmar criacao de novos alertas e casos.
- Validar ausencia de crescimento anormal em `ingest_errors`.
- Validar smoke E2E de `global-search`, `mappings`, `player-lists`, `ingest-jobs`, `ingest-errors`, `feature-store`, `model-registry`, `audit-logs`, `reports`, `notifications`, `admin/settings`, `admin/ops` e `api-keys`.
- Validar isolamento multi-tenant em ingestao: tenant secundario nao deve consultar/reprocessar `ingest_job` nem `ingest_error` de outro tenant (esperado 404 ou lista vazia).
- Validar suite extended de `mappings-versioning`, `ingest-operations`, `report-exports`, `maintenance-mode`, `report-audit` e `onboarding` antes do go-live.
- Validar suite security de RBAC/PII com `ADMIN`, `AML_ANALYST` e `AUDITOR`.
- Registrar status final do go-live no canal de operacao.

## 7. Evidencia de execucao (2026-03-23)

- Stack validado com `docker compose -f infra/docker-compose.yml ps` (todos os servicos `Up`, incluindo `api`, `stream-processor`, `rules-engine`, `ml-service`, `postgres`, `redis`, `redpanda`, `minio`, `clickhouse`, `prometheus`, `grafana`, `alertmanager`, `frontend`).
- Health agregado validado com `curl http://localhost:8000/health` retornando `status=ok` e checks `postgres/redis/kafka/minio/clickhouse/ml_service/rules_engine/stream_processor`.
- Unit tests validados em lotes:
  - `pytest -q tests/unit/test_alerts.py ... tests/unit/test_features.py --tb=short` => `252 passed`.
  - `pytest -q tests/unit/test_infra_resilience.py --tb=short` => `10 passed`.
  - `pytest -q tests/unit/test_ingest_core.py ... tests/unit/test_tracing_clients.py --tb=short` => `368 passed`.
- Integracao completa validada:
  - `TEST_STACK_UP=1 API_URL=http://localhost:8000 ML_URL=http://localhost:8001 pytest -q tests/integration/ -v --tb=short` => `104 passed`.
- E2E validado:
  - `npm run test:smoke` => `33 passed` (4 blocos).
  - `npm run test:extended` => `13 passed` (10 suites).
  - `npm run test:security` => `3 passed` (RBAC/PII).
  - `npm run test:nightly` => `smoke + extended + security` aprovado.
