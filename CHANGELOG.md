# Changelog

All notable changes to BetAML are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0-rc2] — 2026-04-03

### Added — Production Readiness (11 GAPs closed)

#### Alerting & Notifications (GAP-1 🔴)
- **`infra/alertmanager.yml`**: Alertmanager agora envia notificações reais — email para `OPS_EMAIL` + Slack `#betaml-critical` (critical-receiver) e Slack `#betaml-alerts` (warning-receiver).
- **`infra/docker-compose.yml`**: env vars `OPS_EMAIL` e `SLACK_WEBHOOK_URL` adicionadas ao alertmanager.

#### Secret Manager Abstraction (GAP-2 🔴)
- **`services/api/config.py`**: abstração `_resolve_secrets_from_provider()` com suporte a `SECRETS_PROVIDER=env|aws|azure`. AWS Secrets Manager lê via `SECRET_ARN` ou `SECRETS_PREFIX`; Azure Key Vault via `AZURE_VAULT_URL` + `DefaultAzureCredential`. Secrets injetados em `os.environ` antes do Settings() ser instanciado.

#### Backup Habilitado (GAP-3 🔴)
- **`helm/betaml/values.yaml`**: `backup.enabled: true`, `retentionDays: 30`.

#### CD Pipeline Automatizado (GAP-4 🔴)
- **`.github/workflows/deploy-staging.yml`**: workflow completo — build matrix (6 serviços → GHCR), Helm upgrade `--atomic` com `values-staging.yaml`, rollout status verify, smoke test de health, notificação Slack.

#### Distributed Tracing (GAP-6 🟠)
- **`libs/telemetry.py`**: reescrito com OpenTelemetry real — `TracerProvider`, `OTLPSpanExporter` (gRPC), `BatchSpanProcessor`. Falls back a no-op quando `OTEL_EXPORTER_OTLP_ENDPOINT` não está setado. API pública inalterada (`init_opentelemetry_stub`).
- **`libs/pyproject.toml`**: dependências `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` adicionadas.
- **`infra/docker-compose.yml`**: serviço **Jaeger** (all-in-one v1.56) na porta 16686 (UI) / 4317 (OTLP gRPC). `OTEL_EXPORTER_OTLP_ENDPOINT` setado nos 4 serviços de aplicação.

#### ORM Ghost Columns (GAP-7 🟠)
- **`infra/migration_v28.sql`**: `features JSONB`, `cluster_id INTEGER`, `cluster_size INTEGER` na tabela players + índice parcial.
- **`services/api/models.py`**: colunas `features`, `cluster_id`, `cluster_size` no model Player.
- **`services/ml_trainer/network_clustering.py`** e **`recurrence_estimator.py`**: removidas guards `hasattr`, usa colunas ORM diretamente.

#### Coverage Threshold (GAP-8 🟠)
- **`.github/workflows/ci.yml`**: `--cov-fail-under=40` → `--cov-fail-under=65`.

#### Log Aggregation (GAP-9 🟠)
- **`infra/docker-compose.yml`**: serviços **Loki** (v2.9.6) e **Promtail** (v2.9.6) para coleta centralizada de logs Docker.
- **`infra/configs/loki.yml`**: config Loki single-instance com TSDB schema v13, retenção 30 dias.
- **`infra/configs/promtail.yml`**: Docker service discovery, filtra containers `betaml-*`, parse JSON structlog (level, event, timestamp).
- **`infra/grafana/provisioning/datasources/prometheus.yml`**: datasources **Loki** e **Jaeger** adicionados com campo derivado TraceID para correlação logs→traces.

#### Staging Values (GAP-10 🟠)
- **`helm/betaml/values-staging.yaml`**: overrides de staging — recursos reduzidos, 2 replicas API (max 4), letsencrypt-staging, backup habilitado.

#### Health Endpoints (GAP-11 🟠)
- **`services/stream_processor/main.py`** e **`services/rules_engine/main.py`**: servidor HTTP de health (`/health/live`, `/health/ready`) em threads separadas para probes K8s.

#### Frontend Health (GAP-15 🟡)
- **`services/frontend/app/api/health/route.ts`**: endpoint `/api/health` para probes K8s.

---

## [1.0.0-rc] — 2026-03-25

### Added — COAF Siscoaf 97 Compliance (Portaria SPA/MF 1.143/2024)
- **`services/api/routers/cases.py`**: tabela completa de 22 códigos de ocorrência Siscoaf (1407–1428) e 4 tipos de envolvimento (1/Titular, 8/Outros, 49/Apostador, 50/Usuário) como constantes de módulo (`SISCOAF_OCCURRENCE_CODES`, `SISCOAF_INVOLVEMENT_TYPES`).
- **`ReportPackageIn`**: 5 novos campos — `occurrence_codes`, `involvement_types`, `valor_premio`, `valor_apostas`, `informacoes_adicionais` — com validação automática para `FILE_SAR` (obrigatório código + informações adicionais).
- **XML COAF** (`download_coaf_xml`): novos elementos `<TabelaOcorrencias>`, `<TiposEnvolvimento>`, `<PortariaReferencia>` e `<ComunicadoSiscoaf>` conforme arts. 24–25 da Portaria SPA/MF 1.143/2024.
- **PDF COAF** (`_build_report_pdf`): seção Siscoaf completa com tabela de resumo (valor_premio, valor_apostas, informações adicionais), tabela de códigos (cabeçalho vermelho) e tabela de tipos de envolvimento (cabeçalho roxo).
- **`services/frontend/lib/api.ts`**: constantes `SISCOAF_OCCURRENCE_CODES` e `SISCOAF_INVOLVEMENT_TYPES` exportadas; interface `ReportPackageBody` com todos os 7 campos; assinatura `generateReportPackage` atualizada.
- **`services/frontend/app/(protected)/cases/[id]/page.tsx`**: seção Siscoaf na aba `TabDecision` — lista scrollable com 22 checkboxes de códigos, grid de 4 tipos de envolvimento, inputs numéricos para valores, textarea para informações adicionais, validação inline para `FILE_SAR`, botão desabilitado até requisitos serem preenchidos.

### Added — Performance: CPF indexed O(1) lookup
- **`services/api/models.py`**: coluna `cpf_hmac VARCHAR(64) INDEX` no modelo `Player` (nullable para retrocompatibilidade).
- **`services/api/auth.py`**: função `compute_cpf_hmac(cpf_plain)` com HMAC-SHA256 e separação de domínio (`sha256(pii_key + ":cpf_hmac")`); import `hmac` adicionado.
- **`services/api/routers/search.py`**: busca de CPF de 11 dígitos via `cpf_hmac` indexado O(1); fallback bounded O(n ≤ 250) para parciais de 3–10 dígitos.
- **`infra/migration_v21.sql`**: `ALTER TABLE players ADD COLUMN IF NOT EXISTS cpf_hmac VARCHAR(64)` + `CREATE INDEX CONCURRENTLY idx_players_tenant_cpf_hmac`.
- **`scripts/backfill_cpf_hmac.py`**: script de backfill em lotes com `--batch-size`, `--dry-run`, Fernet decrypt + recálculo HMAC para registros existentes.

### Changed — cpf_hmac lifecycle consistency
- **`services/api/seeds.py`**: importa `compute_cpf_hmac`; todos os 50 players de seed criados com `cpf_hmac` preenchido.
- **`services/api/routers/players.py`**: alias de erasure LGPD (`right_to_erasure_alias`) popula `cpf_hmac` do CPF anonimizado.
- **`services/api/repositories/players.py`** (`mark_erased`): define `cpf_hmac = None` junto com zeros de `cpf_encrypted` / `name_encrypted`.
- **`services/api/jobs.py`** (LGPD auto-expiração): define `cpf_hmac = None` na anonimização automática por `data_retention_days`.

### Security
- **`services/api/main.py`**: `JWT_SECRET` exige mínimo de 32 bytes no startup (OWASP — HMAC-SHA256 requer ≥ 256 bits); `epsilon_webhook_secret` com valor padrão de dev levanta `RuntimeError` em produção.

---

## [0.9.2] — 2026-03-24

### Added — E2E resiliency hardening
- `e2e/README.md` com runbook da suite Playwright, variaveis de ambiente e troubleshooting operacional.
- Wrapper E2E `e2e/scripts/run-playwright.sh` com preflight de stack (frontend + API), retry para falhas transitórias e snapshot automatico de diagnostico Docker (`docker compose ps` + logs de `api`/`frontend`).
- Workflow `.github/workflows/e2e.yml` com health gate explicito antes da execucao da suite.
- Novo artefato de CI `artifact-e2e-wrapper-run-log` com log consolidado da execucao E2E.

### Changed
- `e2e/tests/helpers.ts` (`createMappingViaApi`) com retry para status transitorios (5xx/429/408) configuravel por `E2E_API_RETRIES`.
- Falhas finais de criacao de mapping agora incluem status HTTP e preview de body para RCA rapido em CI.
- `docs/contributing.md` e `README.md` atualizados para referenciar o guia E2E e o novo artefato de log.

## [0.9.1] — 2026-03-13

### Added — Migration governance & operations
- Alembic baseline scaffold em `services/api/alembic/` (`env.py`, `alembic.ini`, template e revisao `20260313_000001`)
- CI: novo job `Alembic Baseline Check` em `.github/workflows/ci.yml` para validar heads/history
- Docs operacionais atualizados com bootstrap Alembic em `README.md` e `docs/ops-guide.md`
- Workflow manual `.github/workflows/release-readiness.yml` com gate completo (Alembic, migracao legada dry-run, stack smoke e Playwright auth/cases) usando inputs `e2e_username` e `e2e_password`
- Dashboard `betaml-reliability-slo` provisionado em Grafana para acompanhar disponibilidade, latencia p95 e erro 5xx
- Novas regras SLO no Prometheus (`infra/prometheus_alert_rules.yml`) com recording rules e alertas de budget burn
- Endpoint de scorecard AML `GET /admin/kpis/aml` (triagem, rotulagem e SLA de casos)
- Documentacao do scorecard em `docs/aml-scorecard.md`
- Teste de integracao para KPI AML em `tests/integration/test_new_endpoints.py`
- Workflow diario de qualidade de dados AML em `.github/workflows/data-quality.yml`
- Workflow semanal de capacidade com Locust em `.github/workflows/capacity-smoke.yml`
- Script `scripts/data_quality_checks.py` com checks criticos de consistencia
- Script `scripts/apply_branch_protection.sh` + guia `docs/branch-protection.md` para enforcement de merge policy

### Changed
- Roadmap de migracoes passa a operar em modelo hibrido: Alembic formal + script idempotente como fallback legado

## [0.9.0] — 2026-03-13

### Added — CI/CD & Kubernetes
- GitHub Actions CI pipeline (`.github/workflows/ci.yml`): backend tests + coverage gate (≥40%), ruff lint, TypeScript type-check, Bandit security scan (SARIF upload to GitHub Security tab), Docker build validation
- GitHub Actions E2E workflow (`.github/workflows/e2e.yml`): manual trigger + weekly schedule; spins full docker compose stack, runs Playwright against it
- Playwright E2E test suite (`e2e/`): auth (login/redirect/error/unauthenticated), dashboard (KPI cards, navigation), alerts (page load, filters, search, detail), cases (list, tabs, detail navigation)
- Helm chart (`helm/betaml/`): production-ready chart with API Deployment+HPA, frontend Deployment+Service, PostgreSQL StatefulSet, Redis Deployment, ML Trainer CronJob, Ingress, ConfigMap, and `_helpers.tpl`

### Fixed — Frontend TypeScript
- `admin/page.tsx`: removed duplicate component body (lines 581–950) that caused TS2451/TS2393 errors
- `model-registry/page.tsx`: removed duplicate component body (lines 241–351)
- `cases/page.tsx`: eliminated all `(c as Record<string,unknown>)` casts — fields already typed in `Case` interface; removed unused `useSearchParams` import
- `dashboard/page.tsx`: removed three redundant `Record<string,unknown>` casts for `sla_due_at`, `reference_number`
- `cases/[id]/page.tsx`: removed dead `txns.*` block (TS2304 undefined variable), fixed `ev.content?.comment` → `!!ev.content?.comment` (TS2322 `unknown` in JSX), removed duplicate `SLABadge` function
- `settings/page.tsx`: guarded `new Date(null)` — `config.updated_at` is `string | null`

---

## [0.8.0] — 2026-02-28

### Fixed — Dead imports / timezone / frontend field alignment
- `main.py`: removed ~35 dead imports left after `routes_enterprise.py` deletion; fixed `datetime.utcnow()` in health endpoint
- `auth.py`, `utils.py`, `routers/alerts.py`, `routers/cases.py`: all `datetime.utcnow()` → `datetime.now(timezone.utc)` (deprecation + LGPD timestamp accuracy)
- `routers/compound_rules.py`: import `CompoundRuleOut` + `RuleMacroOut` from `libs.schemas` (was missing `created_at`/`tenant_id`; fixed `expression` vs `body_dsl` field mismatch)
- `frontend/rules/compound/page.tsx`: `component_rules` → `component_rule_ids` (was silently submitting empty list); removed non-existent `action` field; column now shows `logic`
- `frontend/lib/api.ts`: `FeatureStoreHistoryItem.snapshot_date` `string` → `string | null`
- `frontend/notifications/page.tsx`: replaced local interface + fetch functions with canonical imports from `lib/api`

---

## [0.7.0] — 2026-02-14

### Removed
- `routes_enterprise.py` deleted — all 19 routes extracted to dedicated routers; `enterprise_router` removed from `main.py`

### Added / Fixed — Schema & data integrity
- `models.py`: `Alert.label_note = Column(Text)` for analyst investigation notes
- `infra/migration_v12.sql`: `ALTER TABLE alerts ADD COLUMN label_note TEXT`
- `routers/alerts.py`: `AlertLabelIn.label_note Optional[str]`; persists to DB; audit entry includes `label_note`; removed duplicate logger
- `routers/compound_rules.py`: `CompoundRuleOut.logic Optional[str]`; `CompoundRuleCreate.logic = Field("AND", max_length=10)`
- `libs/schemas.py`: 3× `utcnow()` → timezone-aware lambda; `FeatureStoreHistoryItemOut.snapshot_date` `str` → `Optional[date]`; `ApiKeyCreate.scopes` → `permissions`; `CompoundRuleCreate.logic` max_length=10
- `routers/admin.py`: `body.scopes` → `body.permissions`
- `infra/docker-compose.yml`: mount `migration_v12.sql`
- `docs/ops-guide.md`: v12 in migration loop + summary table + verification check
- `tests/unit/test_lgpd_erasure.py`: removed dead `_load_routes_enterprise` function + unused `types` import

---

## [0.6.0] — 2026-01-31

### Added — Route extraction & integration tests
- New routers extracted from `routes_enterprise.py`: `player_lists.py`, `compound_rules.py`, `reports.py`
- Alert labeling moved to `routers/alerts.py`; SSE stream moved to `routers/ingest.py`
- `tests/unit/test_jobs.py`: `check_sla_violations` + `compute_feature_population_stats` coverage
- `tests/unit/test_ml_trainer.py`: 16 unit tests with `_SQLColMock` pattern for Python 3.12
- `tests/integration/test_new_endpoints.py`: 19 integration tests (search, erase, COAF XML, webhook, user CRUD, model promote, compound rules, macros, player-lists)
- `frontend/admin/onboarding/page.tsx`: loads API rules via `fetchRules()` + merges with `RULE_TEMPLATES` fallback

### Fixed
- `routers/alerts.py` `_enqueue_feedback_event`: lazy `from main import get_producer` (avoids opening new Kafka connection per request)
- Frontend: CNPJ field, COAF XML button, Users admin tab, stats dashboard, Tab Movimentações, `search` risk_band fix

---

## [0.5.0] — 2026-01-17

### Added — Production readiness
- `seeds.py`: idempotency check (skip if tenants table already populated)
- `rate_limit.py`: shared SlowAPI `Limiter` (Redis-backed, tenant-id key, 1000/min + 10000/hr)
- `main.py`: `SlowAPIMiddleware` auto-applies limits to all routes; `RateLimitExceeded` handler; SLA violations job (hourly)
- `config.py`: `@model_validator(mode="after")` rejects insecure `JWT_SECRET` / `PII_ENCRYPTION_KEY` at instantiation
- `routers/players.py`: `POST /players/{id}/erase` (LGPD Art. 18 erasure, ADMIN only)
- `routers/admin.py`: `GET /admin/tenants` + `PATCH /admin/tenants/{id}` (TenantOut, user_count, SUPER_ADMIN)
- `routers/cases.py`: `reference_number`, `priority`, `sla_due_at` in list + get; COAF XML endpoint
- `jobs.py`: `check_sla_violations()` with 2-hour dedup; notifies assigned analyst + ADMIN users
- `infra/migration_v11.sql`: 30+ performance indexes (alerts, cases, players, transactions, feature_snapshots, notifications, rules)
- `infra/alertmanager.yml` + `infra/prometheus_alert_rules.yml`: AlertManager routing; 10 Prometheus alert rules
- Grafana dashboards: `betaml-business.json` (8 panels), `betaml-infrastructure.json` (6 panels)
- `scripts/clickhouse_backfill.py`: historical backfill CLI (90-day window, optional tenant filter)
- `frontend/admin/page.tsx`: full rewrite — tabbed admin panel (Operadores / Chaves / Flags / Usuários)
- `docs/security-secrets-management.md`: guide for AWS SM / Azure KV / Vault / K8s ESO + PII rotation
- `docs/ml-trainer-implementation.md`: ML training scheduler specification

---

## [0.4.0] — 2026-01-03

### Added — Second-wave audit gaps
- `libs/schemas.py`: `ApiKeyOut` fields aligned (`id→str`, `scopes→permissions`, `is_active→active`); `SystemFlagOut` `key/value`; `ModelRegistryOut`
- `routers/admin.py`: `SystemFlag` composite key `f"{tenant_id}:{flag_name}"`; `ApiKey` field alignment
- `routers/audit.py`: full rewrite with `date_from`/`date_to` query params; `pii_accessed` extracted from action string
- `tests/security/test_tenant_isolation.py`: 7 cross-tenant isolation tests
- `tests/unit/test_jobs.py`: 7 tests for `risk_score_decay` + `lgpd_expiration`
- `frontend/lib/api.ts`: `Notification`, `ModelRegistry` interfaces; `data_retention_days` in `ScoringConfig`
- `infra/migration_v9.sql`: `reference_type`/`reference_id` in notifications; `chk_player_status` constraint
- `routers/feature_store.py`: history filters use `snapshot_date` (not `created_at`)
- `routers/players.py`: ERASED player returns HTTP 410
- `routers/ml.py`: `response_model=list[ModelRegistryOut]`
- `docs/analyst-guide.md` Sections 8.3 (COAF SAR), 13 (Notifications), 14 (Model Registry)
- `docs/ops-guide.md` Section 4: full v2–v9 migration runbook with summary table

---

## [0.3.0] — 2025-12-20

### Added — Monitoring & ML Trainer service
- `services/ml_trainer/main.py`: IsolationForest retrainer (APScheduler 03:00 UTC, MinIO persistence, F1>0.75 auto-champion)
- `services/ml_trainer/requirements.txt` + `Dockerfile`
- `infra/docker-compose.yml`: `ml-trainer` service; `routers/internal.py`: `POST /internal/alerts/webhook` (AlertManager receiver)
- `infra/migration_v10.sql`: `feature_version` column on `feature_snapshots`

---

## [0.2.0] — 2025-12-06

### Added — Core compliance & background jobs
- `jobs.py`: `risk_score_decay` (04:00 UTC), `lgpd_data_expiration` (05:00 UTC)
- `utils.py`: `write_audit()` extended with `pii_accessed` param (LGPD Art. 37)
- `routers/players.py`: PII audit when `show_full=True`
- `main.py`: APScheduler integrated in startup event
- New routers extracted: `admin.py`, `feature_store.py`, `ml.py`, `notifications.py`
- `routers/admin.py`: `POST /admin/tenants` (tenant onboarding)
- `routers/cases.py`: `POST /cases/{id}/report-package/submit` (COAF stub)
- `tests/unit/test_infra_resilience.py`: 10 resilience tests
- `docs/ops-guide.md` Section 14: key rotation procedures
- `.env.example`: all secrets with dev defaults
- `infra/docker-compose.yml`: `${VAR:-default}` pattern throughout
- `infra/migration_v8.sql`: `is_read` on notifications; `migration_v7.sql`: `snapshot_date` on feature_snapshots

---

## [0.1.0] — 2025-11-22

### Added — Initial platform
- FastAPI backend with JWT/RBAC authentication (`SUPER_ADMIN`, `ADMIN`, `AML_ANALYST`, `AUDITOR`)
- PostgreSQL 16 schema: tenants, users, players, transactions, alerts, cases, rules, notifications, feature_snapshots, api_keys, system_flags, model_registry, audit_events
- Redpanda/Kafka event pipeline: `raw.*` → stream processor → `canonical.*` → rules engine → `scoring.alerts` → alert processor → Postgres
- ClickHouse 24 for analytics; MinIO for model artifact storage; Redis 7 for caching and rate limiting
- Next.js 14 (App Router, TypeScript, Tailwind) frontend: login, dashboard, alerts, cases, players, rules, settings, admin, model registry, reports
- Seed data for two tenants (OperadorA, OperadorB) with ADMIN + AML_ANALYST + AUDITOR roles each
- `infra/docker-compose.yml`: 13-service stack (api, frontend, postgres, redis, redpanda, clickhouse, minio, grafana, prometheus, stream-processor, alert-processor, rules-engine)
- `infra/migration_v1.sql` through `migration_v6.sql`: incremental schema evolution
- `docs/ops-guide.md`, `docs/analyst-guide.md`: initial operational and analyst documentation

---

[0.9.0]: https://github.com/betaml/betaml/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/betaml/betaml/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/betaml/betaml/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/betaml/betaml/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/betaml/betaml/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/betaml/betaml/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/betaml/betaml/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/betaml/betaml/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/betaml/betaml/releases/tag/v0.1.0
