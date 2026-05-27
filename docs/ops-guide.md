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

Dashboards provisionados:
- `betaml-overview`
- `betaml-business`
- `betaml-infrastructure`
- `betaml-reliability-slo`
```

## 2. Pré-requisitos

| Ferramenta   | Versão mínima |
|-------------|---------------|
| Docker Engine | 25.x         |
| Docker Compose | v2.24         |
| RAM disponível | 8 GB         |
| Disco livre   | 20 GB         |

## 2.1 Gate de Readiness (pre-release)

Existe um workflow manual de readiness completo em `.github/workflows/release-readiness.yml`.
Ele valida em sequencia:

1. Cadeia Alembic (`heads` e `history`)
2. Preflight operacional completo (`scripts/readiness_preflight.sh`)
3. Restore drill em banco isolado (`scripts/restore_drill.sh`)
4. Capacity smoke com Locust + validacao objetiva de thresholds
5. Smoke E2E, extended e security
6. Gate final de go/no-go (`scripts/release_decision_gate.sh`)

Observacao operacional:
- O gate final valida automaticamente o timestamp do `backup_reference` no formato `YYYYMMDDTHHMMSSZ`.
- Por default, backups com idade maior que 24h resultam em `NO_GO`.
- Override controlado: `--max-backup-age-hours N` ou `--skip-backup-age-check`.
- O gate final exige `--external-provider` com provider real ativo no corte.
- `mock`/`mock_identity` resultam em `NO_GO` (exceto override explícito `--allow-mock-provider`, apenas para ambiente controlado).
- Em corte formal, rode o preflight com validação de provider real:
  - `bash scripts/readiness_preflight.sh --require-real-provider --expected-provider <provider_real>`
  - o preflight valida `EXTERNAL_VALIDATION_PROVIDER` (nao-mock), `EXTERNAL_VALIDATION_PROVIDER_URL` e `EXTERNAL_VALIDATION_PROVIDER_TOKEN`.

Requer configuracao no repositorio GitHub:
- variable `E2E_USERNAME`
- secret `E2E_PASSWORD`

Preflight do repositorio:

```bash
bash scripts/check_github_actions_readiness.sh
```

Ultima reexecucao local validada no branch atual (2026-04-07):
- `artifacts/readiness/preflight.txt` -> `readiness_preflight=PASS`
- `artifacts/readiness/github-actions-readiness.txt` -> `github_actions_readiness=PASS`
- `artifacts/readiness/restore-drill.txt` -> `restore_drill=PASS`
- `artifacts/readiness/capacity/betaml_load_slo.txt` -> `load_slo=PASS`
- `artifacts/readiness/release-go-no-go.txt` -> `release_go_no_go=GO`

Fechamento remoto final do mesmo gate (2026-05-11):
- `artifacts/readiness/release-readiness-remote.txt` -> `release_readiness_remote=PASS`
- workflow `Release Readiness` no GitHub Actions concluido com `success` na run `25696032708` para o head `74c9e14`

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

### 3.3 Bootstrap de acesso inicial

```bash
cd services/api
python seeds.py
```

O bootstrap recomendado cria credenciais deterministicas para ambiente local:
- `superadmin` / `superadmin123` com papel `BetAML_SuperAdmin` para operacoes de plataforma;
- `admin_a` / `admin123` no tenant `operador_a` para fluxos tenant-scoped;
- usuarios `analyst_*` e `auditor_*` para validacao de RBAC.

As variaveis `SUPER_ADMIN_USER`, `SUPER_ADMIN_EMAIL` e `SUPER_ADMIN_PASS` podem sobrescrever o principal de plataforma quando necessario.

## 4. Migrações de Banco de Dados

### Ordem de Execução

Execute as migrations em ordem crescente de número. O arquivo `init-db.sql` cria o schema base (v1). As migrations incrementais adicionam tabelas e colunas conforme o projeto evolui.

```bash
# Recomendado para banco existente (idempotente + controle de checksum)
bash scripts/postgres_migrate_existing.sh

# Apenas visualizar plano (sem aplicar)
bash scripts/postgres_migrate_existing.sh --dry-run
```

### Roadmap (Migrations)

- O script `scripts/postgres_migrate_existing.sh` cobre o cenário atual de recuperação e drift em ambientes legados.
- Alembic baseline disponivel em `services/api/alembic/` para trilha transacional formal.
- Durante a transicao, mantenha o script como fallback operacional para ambientes ja existentes.

### Alembic (Bootstrap Operacional)

```bash
cd services/api

# Valida revisoes
alembic -c alembic.ini heads

# Banco novo: aplica baseline
alembic -c alembic.ini upgrade head

# Banco existente: marca baseline sem executar DDL
alembic -c alembic.ini stamp 20260313_000001
```

Use `stamp` somente quando o schema ja estiver sincronizado (ex.: ambiente legado com migracoes SQL aplicadas).

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
| v14 | Índices de performance para SLA dashboard e sino de notificações (Module 5) |
| v15 | Suporte a refresh token rotation: `users.refresh_token_jti` + índice |
| v16 | A/B testing traffic split: `scoring_configs.ml_challenger_pct` + tabela `model_inference_logs` (analytics) |

### Aplicar Migration Individual

```bash
# Exemplo: aplicar apenas a v9
docker compose -f infra/docker-compose.yml exec -T postgres \
  psql -U betaml -d betaml_dev < infra/migration_v9.sql
```

### Verificar Migrações Aplicadas

```bash
# Checar se coluna snapshot_date existe (v7)
docker compose exec postgres psql -U betaml -d betaml_dev -c \
  "SELECT column_name FROM information_schema.columns
   WHERE table_name='feature_snapshots' AND column_name='snapshot_date';"

# Checar constraint de status (v9)
docker compose exec postgres psql -U betaml -d betaml_dev -c \
  "\d players" | grep chk_player_status

# Checar coluna feature_version (v10)
docker compose exec postgres psql -U betaml -d betaml_dev -c \
  "SELECT column_name, data_type, column_default
   FROM information_schema.columns
   WHERE table_name='feature_snapshots' AND column_name='feature_version';"

# Checar índices de performance criados na v11
docker compose exec postgres psql -U betaml -d betaml_dev -c \
  "SELECT indexname FROM pg_indexes WHERE schemaname='public'
   AND indexname LIKE 'idx_%' ORDER BY indexname;" | wc -l
# Deve retornar ≥ 30 índices

# Checar coluna label_note em alerts (v12)
docker compose exec postgres psql -U betaml -d betaml_dev -c \
  "SELECT column_name FROM information_schema.columns
   WHERE table_name='alerts' AND column_name='label_note';"

# Checar controle de migrations aplicadas pelo script idempotente
docker compose -f infra/docker-compose.yml exec -T postgres \
  psql -U betaml -d betaml_dev -c \
  "SELECT filename, applied_at FROM schema_migrations ORDER BY applied_at DESC LIMIT 10;"
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

## 5.1 Operações de Ingestão (Módulo 1)

### Contrato oficial de ingestão

- Documento canônico: `docs/ingest-contract.md`
- Modo oficial atual: `canonical-first`
- Endpoint operacional: `GET /ingest/contract`

### Política de auto-case

- O materializador oficial de auto-case é o `rules_engine`.
- O endpoint `GET /admin/auto-case-policy` expõe `auto_case_threshold`, gatilhos por severidade e o status do `alert_processor` legado.
- O `alert_processor` legado permanece opt-in apenas para migração controlada em `development` e `test`.
- Em operação normal, evitar criação automática de cases fora do `rules_engine` para não duplicar materialização.
- Metrica de monitoramento: `betaml_ingest_contract`

Antes de qualquer go-live, valide que o contrato retornado pelo endpoint coincide com o contrato documentado.

### Backfill de dados históricos

Use o fluxo de backfill quando houver arquivos legados, correção de mapping ou onboard de um tenant antigo.

Passos recomendados:

1. Garanta que o `MappingConfig` da origem esteja validado e ativo.
2. Faça upload do arquivo histórico por `POST /ingest/file`.
3. Monitore `GET /ingest/jobs` e `GET /ingest/jobs/{id}`.
4. Se houver falhas de mapping, trate na quarentena e faça replay.
5. Quando necessário, reprocessar o Bronze com a versao correta de mapping.

Observação operacional:

- Se nenhum `mapping_config_id` for informado, a API resolve automaticamente a versão ativa do tenant para `source_system + entity_type`.
- O `IngestJob` persistido registra a versão efetiva usada em `mapping_config_id` e `mapping_version_id`.
- Os endpoints `POST /ingest/event` e `POST /ingest/batch` também aceitam `mapping_config_id` para aplicar uma versão imutável específica antes do publish no Kafka.
- O canal `WS /ingest/ws` aceita `mapping_config_id` por mensagem; o envelope preserva `raw_payload` e publica o payload já remapeado.

Exemplo:

```bash
curl -X POST "http://localhost:8000/ingest/file" \
  -H "Authorization: Bearer <token>" \
  -F "file=@historico-marco.ndjson;type=application/x-ndjson" \
  -F "source_system=ConnectorDelta" \
  -F "entity_type=transaction" \
  -F "mapping_config_id=<mapping-id-opcional>"
```

### Smoke real com pacote FictiBet

Para validar ingestao ponta a ponta com cenarios PLD realistas (canonical + Gamma + Delta + Epsilon), use:

```bash
API_URL=http://localhost:8000 \
USERNAME=admin_a \
PASSWORD=admin123 \
EPSILON_WEBHOOK_SECRET=dev-secret-change-me \
scripts/ingest_fictibet_pack.sh
```

O script publica os arquivos de `datasets/fictibet_pld`, aguarda o job canonical chegar em estado terminal e imprime o detalhe final de todos os jobs criados.

### Reprocessar um IngestJob

1. Acesse `Jobs de Ingestão` no frontend ou chame `POST /ingest/jobs/{id}/reprocess`.
2. Informe o motivo do reprocessamento.
3. Opcionalmente selecione uma versão específica de `MappingConfig`.
4. O backend reenfileira a leitura do objeto Bronze salvo em MinIO.

Exemplo:

```bash
curl -X POST "http://localhost:8000/ingest/jobs/<job-id>/reprocess" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"reason":"mapping ajustado","mapping_version_id":"<mapping-version-id>"}'
```

## 5.2 Operações de ML (Módulo 4)

### Acompanhar champion vs challenger

1. Abra `/model-registry` no frontend.
2. Ajuste a janela para `7d`, `30d` ou `90d`.
3. Revise:
   - precisão estimada e false positive rate do período;
   - comparativo champion vs challenger;
   - distribuição por regra e por modelo.

APIs operacionais equivalentes:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/model-registry/performance/summary?days=30"

curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/model-registry/<model-id>/ab-metrics?days=30"
```

### Promover challenger para produção

```bash
curl -X POST \
  -H "Authorization: Bearer <token>" \
  "http://localhost:8000/model-registry/<model-id>/promote"
```

### Consultar explicabilidade de um alerta ML

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/alerts/<alert-id>/explainability"
```

O payload devolve as 5 features mais relevantes, com valor atual, baseline inferido e contribuição.

### Re-treinar modelos manualmente

Quando houver volume novo de labels, drift de comportamento ou comparativo champion/challenger desfavoravel, execute retreino manual.

O retreino automático do `ml_trainer` avalia o modelo em holdout temporal do período recente, e nao apenas no conjunto de treino, antes de registrar métricas e decidir promocao.

ML service:

```bash
curl -X POST "http://localhost:8001/train"
curl -X POST "http://localhost:8001/train/structuring"
curl -X POST "http://localhost:8001/train/graph"
curl -X POST "http://localhost:8001/train/recurrence"
```

Fluxo operacional recomendado:

1. Rode o treino adequado ao problema.
2. Revise a nova entrada no `Model Registry`.
3. Compare metricas em `/model-registry/{id}/ab-metrics`.
4. Promova o challenger apenas se a regressao de precision nao violar o gate.

## 5.3 Operações de Casos (Módulo 5)

### Vincular rapidamente alertas e transações a um caso

No detalhe do caso, a caixa de busca rápida agora consulta alertas avulsos e transações do cliente.

API equivalente:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/lookup?q=deposito&scope=all"
```

### Exportar ReportPackage

```bash
# JSON
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-package/json?rp_id=<report-package-id>"

# PDF
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-package/pdf?rp_id=<report-package-id>" \
  --output report.pdf
```

### Submeter reporte com maker-checker

```bash
curl -X POST -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-package/submit"
```

Regras aplicadas:
- o último pacote precisa ter `decisionLegacy=FILE_SAR`
- o mesmo usuário que gerou o pacote não pode submetê-lo

### Consultar contrato operacional de filing

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-filing-contract"
```

- Expõe canal atual (`MANUAL_PORTAL`), requisitos de maker-checker e campos mínimos de cadeia de custódia.
- Útil para runbook e auditoria quando houver mudança de processo (manual -> integração automática).

### Consultar status operacional de filing

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-filing-status"
```

- Retorna `deadline_state` (`OK`, `WARNING`, `BREACH` ou `NO_REPORT`) para o pacote mais recente do caso.
- Expõe `requires_submission`, `protocol_registered` e `warnings[]` para triagem rápida de pendências regulatórias.

### Fila operacional de filing do tenant

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/report-packages/filing-queue?limit=50"
```

- Ordena por criticidade de prazo (`BREACH` -> `WARNING` -> `OK`) e idade do pacote.
- Por padrão traz apenas a versão mais recente por caso; use `include_all_versions=true` para auditoria detalhada.

### Overview agregado de filing (KPI executivo)

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/report-packages/filing-overview"
```

- Retorna totais agregados de pendência (`requires_submission_count`), protocolo pendente (`missing_protocol_count`) e distribuição de prazo (`deadline_state_counts`).
- Use para checkpoint diário de operação e para report rápido em war room.

### Hotlist acionável de filing (execução imediata)

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/report-packages/filing-hotlist?limit=20"
```

- Retorna apenas casos com ação pendente: `SUBMIT_REPORT` ou `REGISTER_PROTOCOL`.
- Ordena por prioridade operacional (`BREACH` -> `WARNING` -> protocolo pendente), facilitando atuação do plantão.

### Validar cadeia de custódia do ReportPackage

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/report-packages/<report-package-id>/chain-of-custody"
```

- O campo `chain_of_custody.integrity_ok=true` confirma que o hash armazenado
  (`report_payload_sha256`) coincide com o hash recalculado do payload.
- Em caso de divergência (`integrity_ok=false`), o pacote deve ser tratado como
  incidente de integridade e revalidado antes de qualquer filing regulatório.

### Reconciliação ponta-a-ponta (evento -> alerta -> caso -> report)

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/cases/<case-id>/reconciliation"
```

- O retorno traz `all_stages_ok` e `gaps[]` para identificar rapidamente falhas de encadeamento.
- Use `?rp_id=<report-package-id>` para reconciliar contra um pacote específico quando houver múltiplas versões.

### Corrigir item em Quarentena

1. Acesse `Quarentena de Erros`.
2. Abra o item para inspecionar `error_detail` e `raw_payload`.
3. Use `Replay` para editar o JSON corrigido.
4. Escolha `entity_type` e, se necessário, a versão do `MappingConfig`.
5. O erro original pode ser marcado como resolvido automaticamente.

Exemplo:

```bash
curl -X POST "http://localhost:8000/ingest/errors/<error-id>/replay" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
        "corrected_payload":{
          "event_id":"evt-fix-1",
          "external_player_id":"CPF123",
          "transaction_type":"DEPOSIT",
          "amount":120.50,
          "occurred_at":"2026-03-20T12:00:00Z",
          "currency":"BRL"
        },
        "entity_type":"TRANSACTION",
        "resolve_original":true
      }'
```

### Rollback de MappingConfig

1. Acesse `Conectores` no frontend.
2. Selecione o mapping.
3. Abra a lista de versões.
4. Acione `Rollback` na versão desejada.

Exemplo:

```bash
curl -X POST "http://localhost:8000/mappings/<mapping-id>/rollback?version_number=2" \
  -H "Authorization: Bearer <token>"
```

Semântica atual:

- o rollback não reativa a linha antiga em place;
- o backend cria uma nova versão imutável ativa baseada na versão escolhida;
- a resposta inclui `rollback_source_version_number` para auditoria e automação.

Checklist antes do rollback:

- confirmar que a versao alvo possui preview valido
- registrar motivo no ticket interno ou runbook
- verificar impacto em jobs pendentes e reprocessamentos
- se necessario, reprocessar os jobs Bronze afetados apos o rollback

### Streaming Operacional

- `GET /ingest/stream` expõe snapshot SSE com `active_jobs`, `failed_jobs_24h`, `unresolved_errors`, `quarantine_breakdown`, métricas de fila do WebSocket e últimos jobs com falha.
- `WS /ingest/ws` aceita ingestão contínua com fila limitada, `mapping_config_id` por mensagem e resposta de backpressure.
- Eventos que falham após `DLQ_MAX_RETRIES` são publicados em `<topic>.dlq`.

Exemplo de evento único com mapping explícito:

```bash
curl -X POST "http://localhost:8000/ingest/event" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "source_system": "BackofficeAlpha",
    "entity_type": "TRANSACTION",
    "mapping_config_id": "<mapping-version-id>",
    "payload": {
      "customer_id": "PLY-900",
      "amount": "77.10",
      "event_id": "evt-explicit-001"
    }
  }'
```

Exemplo de lote com versões explícitas:

```bash
curl -X POST "http://localhost:8000/ingest/batch" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "source_system": "ConnectorGamma",
      "entity_type": "TRANSACTION",
      "mapping_config_id": "<mapping-version-id>",
      "payload": {
        "customer_id": "PLY-777",
        "amount": "55.25",
        "event_id": "evt-batch-001"
      }
    }
  ]'
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

# Restore drill automatizado com evidência anexável
bash scripts/restore_drill.sh \
  --backup-file ./backup_20241201.sql.gz \
  --evidence-out /tmp/betaml-restore-drill.txt

# Alternativa: recuperar o dump direto do bucket de backup
bash scripts/restore_drill.sh \
  --backup-object betaml-backups/postgres/postgres_20241201T020000Z.sql.gz \
  --evidence-out /tmp/betaml-restore-drill.txt
```

Regra operacional:
- nao use restore in-place como primeira resposta a incidente ou rollback;
- restaure primeiro em banco isolado, valide contagens e so entao decida por restauracao produtiva;
- registre no ticket o nome do dump, horario UTC e operador executor.
- anexe a saida do `scripts/restore_drill.sh` como evidencia canonica do restore drill.

### Backup automatizado em Helm

O chart ja possui `backup.enabled=true` e CronJob diario para dump do Postgres + espelhamento do bucket MinIO.

```bash
# Ver CronJob e ultimos jobs
kubectl get cronjob,jobs -n betaml | grep backup

# Disparar backup manual antes do deploy
kubectl create job --from=cronjob/<release>-backup <release>-backup-manual-$(date +%Y%m%d%H%M%S) -n betaml

# Acompanhar evidencias
kubectl logs job/<release>-backup-manual-YYYYMMDDHHMMSS -n betaml
```

### MinIO (modelos ML e evidências)

```bash
# Via mc (MinIO Client)
docker run --rm --network betaml-net \
  minio/mc mirror betaml/betaml-models /backup/models

# Validar objeto de backup e recuperar artefatos para drill
docker run --rm --network betaml-net \
  minio/mc ls betaml/betaml-backups/postgres
docker run --rm --network betaml-net \
  minio/mc mirror betaml/betaml-backups/minio/<timestamp> /backup/minio-restore-drill
```

O `scripts/restore_drill.sh` valida automaticamente a existencia de `postgres/postgres_<timestamp>.sql.gz`
e de pelo menos um artefato em `minio/<timestamp>/`, falhando o drill quando o espelhamento nao existir.

### Redis (features em memória)

Redis é cache volátil — não requer backup. TTL padrão: 4 horas.

### Decisao entre rollback e restore

- Regressao de aplicacao com schema compativel: rollback de imagem/Helm e smoke funcional.
- Falha de migration destrutiva, corrupcao de dados ou perda de artefatos: congelar deploy, confirmar ultimo backup valido e executar restore drill.
- Sem backup valido recente: no-go para corte de trafego; escalar incidente e manter ambiente controlado.

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

# Estatísticas populacionais do tenant (baseline para zscore/percentile_rank)
GET /feature-store/population-stats

# Resumo mais recente de drift e qualidade das features do tenant
GET /feature-store/quality/latest
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
- O histórico canônico retorna `items[]` com `snapshot_date`, `created_at`, `features`, `drift_score`, `entity_type` e `gold_object_path`.
- O cache online é aquecido no startup da API a partir do snapshot Gold mais recente por player.
- `GET /feature-store/population-stats` retorna `computed_at` e a distribuição agregada do tenant usada pela DSL avançada (`zscore`, `percentile_rank`).
- `GET /feature-store/quality/latest` resume os achados do último ciclo de monitoramento, incluindo null-rate, mean drift, `max_drift_score` e se houve notificação para ADMIN.
- O monitor diário de drift marca `drift_score` nos snapshots do dia e envia `Notification(type="FEATURE_DRIFT")` para ADMINs quando detecta aumento anormal de nulos ou mudança forte de distribuição.
- A rota legada `feature-history` expõe aliases compatíveis como `unique_instruments_used_7d` e `bonus_to_real_money_ratio_30d`.

## 11.1 Motor de Risco Operacional

### Simulação de regra

```bash
# Simulação manual com payload de evento/contexto
POST /rules/{rule_id}/simulate

# Simulação histórica por janela de datas
POST /rules/{rule_id}/simulate
{
  "from": "2026-03-01",
  "to": "2026-03-20",
  "player_ids": ["player-123"]
}
```

- A resposta histórica retorna `total_alerts`, `players`, `false_positive_estimated`, `precision_estimated`, `recall_estimated`, `performance_score` e `timeline[]`.
- A validação DSL agora aceita named args como `window="24h"`, `baseline_window="30d"` e `segment="profession"`, além do alias `if(...)`.

### Trilha de impacto de regras

```bash
GET /rules/{rule_id}/impact-trail?limit=50
```

- Retorna trilha auditável de alterações e simulações (`CREATE`, `UPDATE`, `DELETE`, `SIMULATE_RULE`).
- A trilha inclui `before`, `after`, `user_id`, `action` e `created_at` para governança operacional.

### Compound Rules e Player Lists

```bash
GET  /rules/compound
POST /rules/compound
PUT  /rules/compound/{rule_id}

GET    /player-lists
GET    /player-lists/{list_id}
PATCH  /player-lists/{list_id}
GET    /player-lists/{list_id}/entries
POST   /player-lists/{list_id}/entries
DELETE /player-lists/{list_id}/entries/{entry_id}
```

- Compound rules suportam `AND`, `OR` e `N_OF_M`, com `severity_mode=MAX|FIXED`.
- O `rules_engine` aplica pesos do tenant vindos de `scoring_configs` para combinar regra, ML e rede no `score_breakdown`.

### Contratos de listagem com envelope opcional

Para integrações que exigem paginação estável, use `envelope=true`.
Sem esse parâmetro, o retorno legado (lista direta) é preservado.

```bash
GET /rules?envelope=true&limit=50&offset=0
GET /player-lists?envelope=true&limit=50&offset=0
GET /notifications?envelope=true&limit=50&offset=0
GET /audit-logs?envelope=true&limit=50&offset=0
```

Shape de resposta em modo envelope:

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

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
  -F "mapping_config_id=<mapping-id-opcional>" \
  -F "file=@./sample-gamma.xml;type=application/xml"
```

```bash
curl -s -X POST "http://localhost:8000/ingest/connectors/delta/parse" \
  -H "Authorization: Bearer $TOKEN" \
  -F "entity_type=transaction" \
  -F "mapping_config_id=<mapping-id-opcional>" \
  -F "file=@./sample-delta.ndjson;type=application/x-ndjson"
```

Resposta inclui:

- `job_id`
- `source_system`
- `mapping_config_id`
- `mapping_version_id`
- `status` (`DONE`, `PARTIAL` ou `FAILED`)
- `summary.accepted`, `summary.failed`, `summary.total`, `summary.errors`

### 12.5 Runbook DLQ e replay idempotente (PR-05)

Pre-condicoes:

- API e stream_processor saudaveis (`/health`, `/health/ready`).
- Kafka/Redpanda e Redis disponiveis.
- Token com permissao de ingest e replay.

#### 12.5.1 Dry-run (sem publicar replay)

```bash
BETAML_API_URL="http://localhost:8000" \
BETAML_API_TOKEN="$TOKEN" \
python scripts/replay_dlq.py --dry-run --limit 20
```

Resultado esperado:

- Lista de erros elegiveis.
- Mensagem `Dry-run ativo: nenhum replay executado`.

#### 12.5.2 Replay efetivo

Replay de um erro especifico:

```bash
BETAML_API_URL="http://localhost:8000" \
BETAML_API_TOKEN="$TOKEN" \
python scripts/replay_dlq.py --error-id "$ERROR_ID" --reason "dlq replay operacao"
```

Replay em lote (lista retornada por `/ingest/errors`):

```bash
BETAML_API_URL="http://localhost:8000" \
BETAML_API_TOKEN="$TOKEN" \
python scripts/replay_dlq.py --limit 20 --reason "batch replay"
```

#### 12.5.3 Validacao pos-replay

1. Consultar erro reprocessado:

```bash
curl -s "http://localhost:8000/ingest/errors?limit=50" \
  -H "Authorization: Bearer $TOKEN"
```

2. Confirmar comportamento idempotente (segunda tentativa):

```bash
curl -s -X POST "http://localhost:8000/ingest/errors/$ERROR_ID/replay" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"idempotency check"}'
```

Resultado esperado: `status=already_processed` ou `status=queued` conforme janela de processamento.

3. Verificar metricas relacionadas:

- `betaml_stream_dlq_published_total`
- `betaml_stream_messages_failed_total`
- `betaml_stream_dedupe_total`

#### 12.5.4 Evidencia operacional minima

- Comando dry-run executado e output salvo.
- IDs replayados e status final (`queued` ou `already_processed`).
- Snapshot de metricas antes/depois.
- Numero do incidente/change associado.

### 12.6 Semantica de commit e seguranca de offset

- O `stream_processor` opera com `enable_auto_commit=false`.
- Offset e commitado somente apos sucesso de processamento ou apos publicacao em DLQ.
- Em falha de publicacao na DLQ, offset nao e commitado (mensagem volta para consumo).

### 12.7 Padrao de classificacao de erro

- `validation_error`: envelope/payload invalido.
- `transient_error`: falha de infraestrutura (broker/rede/timeout).
- `processing_error`: excecao nao classificada no processamento de negocio.

### 12.8 Topico de DLQ em runtime

- Usa `BETAML_DLQ_TOPIC` quando configurado.
- Se vazio, usa fallback por topico de origem: `<topic>.dlq`.

## 13. Verificações de Isolamento Multi-tenant (Checklist)

Use dois tokens de tenants distintos (`$TOKEN_A` e `$TOKEN_B`) para validar
que recursos de um tenant não são acessíveis pelo outro.

### 13.1 Audit log (novo e legado)

```bash
curl -i -s http://localhost:8000/audit-logs -H "Authorization: Bearer $TOKEN_A"
curl -i -s http://localhost:8000/audit-log  -H "Authorization: Bearer $TOKEN_A"
curl -i -s "http://localhost:8000/audit-logs?action=EXPORT_REPORT_JSON&pii_only=true" \
  -H "Authorization: Bearer $TOKEN_A"
```

Sem token, ambos devem retornar `401`/`403`.

### 13.1.1 Relatório regulatório mensal

```bash
curl -s "http://localhost:8000/reports/monthly-summary?date_from=2026-03-01&date_to=2026-03-31" \
  -H "Authorization: Bearer $TOKEN_A"
curl -OJ "http://localhost:8000/reports/monthly-summary/csv?date_from=2026-03-01&date_to=2026-03-31" \
  -H "Authorization: Bearer $TOKEN_A"
```

Verifique no payload:
- `total_communications_generated`
- `total_sar_reports`
- `quality_metrics.true_positive_rate`
- `quality_metrics.false_positive_rate`

### 13.1.2 Saúde agregada e observabilidade

```bash
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8000/health/ready | jq
curl -s http://localhost:8000/admin/ops/summary -H "Authorization: Bearer $TOKEN_A" | jq
```

Verifique:
- `checks.rules_engine == "ok"`
- `checks.stream_processor == "ok"`
- `kafka_consumer_lag`
- `ingest_error_rate_24h_percent`
- `alerts[]`

### 13.1.3 Métricas Prometheus / Grafana

```bash
curl -s http://localhost:9090/-/ready
curl -s http://localhost:8000/metrics | head
curl -s http://localhost:8001/metrics | head
```

No ambiente docker-compose:
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`
- Alertmanager: `http://localhost:9093`

Métricas esperadas:
- `betaml_kafka_consumer_lag_messages`
- `betaml_rules_events_processed_total`
- `betaml_stream_events_processed_total`
- `betaml_ml_scoring_failures_total`

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
