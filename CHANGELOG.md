# Changelog

All notable changes to BetAML are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

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
