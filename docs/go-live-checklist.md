# BetAML - Go-Live Checklist

Fontes canonicas de readiness do branch atual:
- este checklist, para criterios de go/no-go;
- docs/ops-guide.md e docs/runbook-deploy.md, para execucao operacional;
- artifacts/readiness/, para evidencias objetivas do branch vigente;
- docs/auditoria-consolidada-pld-2026-03-20.md, apenas como contexto historico.

Importante:
- este checklist continua válido como gate operacional, mas não use validações históricas anexadas em março como prova do estado atual do branch;
- toda evidência listada abaixo deve ser regenerada no branch vigente antes de qualquer go/no-go.

Ultima reexecucao local validada no branch atual (2026-04-07):
- [artifacts/readiness/preflight.txt](../artifacts/readiness/preflight.txt): `readiness_preflight=PASS`
- [artifacts/readiness/github-actions-readiness.txt](../artifacts/readiness/github-actions-readiness.txt): `github_actions_readiness=PASS`
- [artifacts/readiness/restore-drill.txt](../artifacts/readiness/restore-drill.txt): `restore_drill=PASS`
- [artifacts/readiness/capacity/betaml_load_slo.txt](../artifacts/readiness/capacity/betaml_load_slo.txt): `load_slo=PASS`, `p95_ms=420.00`, `rps=72.32`, `event_rps=723.19`
- [artifacts/readiness/release-go-no-go.txt](../artifacts/readiness/release-go-no-go.txt): `release_go_no_go=GO`

Fechamento remoto final validado no branch atual (2026-05-11):
- [artifacts/readiness/release-readiness-remote.txt](../artifacts/readiness/release-readiness-remote.txt): `release_readiness_remote=PASS`, run `25696032708`, head `74c9e14`
- Workflow `Release Readiness` concluido em `success` para `main`, com `Run external validation integration smoke` e `Install Playwright deps` aprovados no GitHub Actions.

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
- `scripts/check_github_actions_readiness.sh` retorna `github_actions_readiness=PASS`.

## 4. Validacao funcional

- API health: `GET /health`.
- Frontend login + rotas protegidas.
- Fluxo de alertas: listar, abrir detalhe, triagem e vinculação a caso.
- Fluxo de casos: criar caso, abrir detalhe, comentar, anexar evidência, mudar status, gerar dossie e exportar report package em JSON/PDF.
- Ingestao smoke: `raw.* -> canonical.* -> features.*` observada em logs.
- Gate Python alinhado ao CI: `DEBUG=false bash scripts/run_critical_unit_batches.sh --include-remainder -q --tb=short --cov=services/api --cov-fail-under=40`.
- E2E (pipeline + ML): `TEST_STACK_UP=1 pytest tests/integration/ -v` executado em staging.

## 5. Observabilidade e operacao

- Dashboards Grafana revisados (API latency, DLQ, ingest throughput).
- Alertas ativos: erros de migracao, backlog de fila, 5xx API.
- On-call informado com janela de deploy e rollback procedure.

## 5.1 Backups

- Backup diario habilitado (Helm CronJob) ou rotina equivalente em producao.
- Ultimo backup valido referenciado no ticket de release com timestamp, bucket/caminho e operador responsavel.
- Teste de restore (Postgres + artefatos MinIO) executado em ambiente de homologacao.

## 5.2 Criterios explicitos de go/no-go

Go-live so pode seguir quando todos os itens abaixo estiverem verdes e anexados ao ticket/canal operacional:
- `artifact-readiness-preflight` do workflow manual ou saida equivalente de `scripts/readiness_preflight.sh`.
- `artifact-readiness-go-no-go` com decisao final `release_go_no_go=GO`.
- `artifact-readiness-capacity-smoke` do mesmo readiness com `load_slo=PASS` para o endpoint `POST /ingest/batch`.
- Evidencia do ultimo backup valido com idade inferior a 24h.
- Evidencia de restore drill em banco isolado, sem restore in-place em producao.
- Revisao alvo de rollback definida (Helm revision ou tag de imagem) e responsavel on-call nomeado.
- Smoke funcional pos-deploy concluido sem 5xx persistente, backlog anormal ou falha de tenant isolation.

No-go automatico:
- Sem evidencia objetiva de backup/restaure ou sem operador responsavel pelo rollback.
- Falha no preflight operacional, probes `/health/live` ou `/health/ready`.
- Dependencia externa critica ainda em modo mock, indisponivel ou sem segredo valido.
- Falta de cobertura on-call para os primeiros 60 minutos do go-live.

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

## 7. Evidencia de execucao e anexos obrigatorios

Anexar no go-live atual:
- `artifact-readiness-preflight` com stack, probes HTTP, cadeia Alembic, dry-run de migracao e validacao de backup.
- `artifact-readiness-restore-drill` com restore em banco isolado, validacao do dump e checagem dos artefatos MinIO.
- `artifact-readiness-capacity-smoke` com CSV/HTML do Locust, sumario e validacao de thresholds do `validate_slo.py`.
- `artifact-readiness-go-no-go` com metadados operacionais minimos, validacao dos XMLs JUnit e decisao final do gate.
- Evidencia do fechamento remoto do workflow `Release Readiness` (run `25696032708`) ou export equivalente com `status=completed` e `conclusion=success`.
- Diretorio JUnit equivalente para smoke, extended e security (ex.: `artifacts/readiness/junit/`) quando a execucao for manual.
- `artifact-readiness-playwright-report` e `artifact-readiness-playwright-results` do workflow manual.
- Log do restore drill gerado por `scripts/restore_drill.sh` com contagens basicas (`players`, `alerts`, `cases`) e verificacao dos artefatos MinIO.
- Revisao alvo para rollback e referencia do ultimo backup valido.

## 8. Histórico e evidências antigas

Os resultados históricos usados em março servem apenas como referência de capacidade já atingida em algum momento do projeto.

Eles não substituem:
- preflight do branch atual;
- restore drill atual;
- capacity smoke atual;
- smoke funcional atual;
- suites de integração, E2E e segurança reexecutadas no estado vigente do código.
