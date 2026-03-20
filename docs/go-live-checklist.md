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
- Fluxo de alertas: listar, rotular, mudar status.
- Fluxo de casos: criar caso, abrir detalhe, exportar PDF.
- Ingestao smoke: `raw.* -> canonical.* -> features.*` observada em logs.
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
- Registrar status final do go-live no canal de operacao.
