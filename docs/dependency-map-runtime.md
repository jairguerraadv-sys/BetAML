# BetAML Dependency Map (Runtime)

Last updated: 2026-04-04

Este mapa conecta os eixos mais sensíveis do runtime:
- arquivo de entrada (service/router)
- rota HTTP (quando aplicável)
- tópicos Kafka/Redpanda (in/out)
- tabelas Postgres/ClickHouse e objetos no lakehouse

## 1) End-to-end Critical Path

1. Ingestão entra por [services/api/routers/ingest.py](../services/api/routers/ingest.py) (`/ingest/*`, webhook Epsilon, WebSocket).
2. No runtime atual, a API publica majoritariamente direto em `canonical.*` e também em `ingest.jobs`.
3. [services/stream_processor/main.py](../services/stream_processor/main.py) consome `canonical.*`, persiste OLTP, calcula features e publica `features.player_daily`.
4. [services/rules_engine/main.py](../services/rules_engine/main.py) consome `canonical.*` + `features.player_daily`, avalia DSL e publica `scoring.alerts`.
5. [services/api/alert_processor.py](../services/api/alert_processor.py) permanece desativado por padrão; o caminho operacional ativo de materialização de alertas/casos é apenas [services/rules_engine/main.py](../services/rules_engine/main.py).
6. Frontend consome rotas de `alerts`, `cases`, `reports`, `feature-store`, `admin`.

## 2) File -> Route -> Topic -> Table Map

| File | Rotas/Trigger | Kafka In | Kafka Out | Tabelas / Storage |
|---|---|---|---|---|
| [services/api/routers/ingest.py](../services/api/routers/ingest.py) | `POST /ingest/event`, `POST /ingest/batch`, `POST /ingest/file`, `POST /ingest/webhook/epsilon`, `POST /ingest/jobs/{id}/reprocess`, `GET /ingest/jobs`, `GET /ingest/errors`, `POST /ingest/errors/{id}/replay`, `WS /ingest/ws` | - | `canonical.transactions`, `canonical.bets`, `canonical.device_events`, `ingest.jobs`, `ingest.jobs.reprocess`, `*.dlq` | `ingest_jobs`, `ingest_errors`, `mapping_configs`; Bronze MinIO: `bronze/{tenant}/ingest_jobs/{job}/...` |
| [services/stream_processor/main.py](../services/stream_processor/main.py) | Consumer loop (`TOPICS`) + job processor (`ingest.jobs`, `ingest.jobs.reprocess`) | `canonical.transactions`, `canonical.bets`, `canonical.device_events`, `canonical.kyc_events`, `canonical.responsible_gambling_events`, `canonical.account_status_changes`, `ingest.jobs`, `ingest.jobs.reprocess` | `features.player_daily`, `canonical.*.dlq`, `ingest.jobs.dlq`, `ingest.jobs.reprocess.dlq` | Postgres: `financial_transactions`, `bets`, `device_events`, `feature_snapshots`, `ingest_jobs`, `ingest_errors`, `player_kyc_events`; ClickHouse: `betaml.player_features_daily`; Gold MinIO: `gold/tenant_id=.../feature_date=...` |
| [services/rules_engine/main.py](../services/rules_engine/main.py) | Consumer loop + evaluator | `canonical.transactions`, `canonical.bets`, `features.player_daily` | `scoring.alerts` | Reads: `rule_definitions`, `rule_macros`, `player_lists`, `player_list_entries`, `scoring_configs`; Writes: `alerts`, `cases`, `case_events`, `rule_execution_logs`, `players` |
| [services/api/alert_processor.py](../services/api/alert_processor.py) | Startup hook legado (`start_alert_consumer`, no-op por padrão) | `scoring.alerts` | - | Autoridade operacional removida; `rules_engine` materializa `alerts`, `cases`, `case_events` e atualiza `players` |
| [services/api/routers/rules.py](../services/api/routers/rules.py) | `GET/POST/PUT/DELETE /rules`, `POST /rules/{id}/simulate` | - | - | `rule_definitions`, `rule_execution_logs` (simulação/consulta) |
| [services/api/routers/compound_rules.py](../services/api/routers/compound_rules.py) | `GET/POST/PUT/DELETE /rules/compound`, `GET/POST/DELETE /rules/macros` | - | - | `compound_rules`, `rule_macros` |
| [services/api/routers/player_lists.py](../services/api/routers/player_lists.py) | CRUD `/player-lists` + upload CSV | - | - | `player_lists`, `player_list_entries` |
| [services/api/routers/alerts.py](../services/api/routers/alerts.py) | `GET /alerts`, `GET /alerts/{id}`, `POST /alerts/{id}/triage|close|link-to-case|label` | - | - | `alerts`, `cases` (vínculo), evidências relacionadas |
| [services/api/routers/cases.py](../services/api/routers/cases.py) | CRUD operacional de casos + report package (`/cases/{id}/report-package*`) | - | - | `cases`, `case_events`, `report_packages`, relação com `alerts` |
| [services/api/routers/reports.py](../services/api/routers/reports.py) | `POST/GET /reports/monthly-summary`, `GET /reports/monthly-summary/csv` | - | - | Agrega `alerts`, `cases`, `report_packages`, `rule_definitions`, `players` |
| [services/api/routers/feature_store.py](../services/api/routers/feature_store.py) | `GET /feature-store/players/{id}/history|current`, `GET /feature-store/population-stats` | - | - | `feature_snapshots` + Redis online feature store |
| [services/api/routers/players.py](../services/api/routers/players.py) | `GET /players/{id}/feature-history`, charts e rede, LGPD erase/export | - | - | `players`, `financial_transactions`, `bets`, `alerts`, `cases`; ClickHouse `betaml.player_features_daily` |
| [services/ml_service/main.py](../services/ml_service/main.py) | `POST /score`, `POST /train`, `POST /score/ab`, `POST /score/shap`, `GET /models*` | - | - | Reads ClickHouse `betaml.player_features_daily`; Writes `model_registry`, `model_inference_logs` |
| [services/api/routers/ml.py](../services/api/routers/ml.py) | `GET /model-registry*`, `POST /model-registry/{id}/promote|challenger` | - | - | `model_registry`, métricas A/B, promoção champion/challenger |
| [services/api/routers/admin.py](../services/api/routers/admin.py) | tenant settings, users, flags, scoring config, api keys, maintenance mode | - | `ingest.jobs` (onboarding sample) | `tenants`, `users`, `api_keys`, `system_flags`, `scoring_configs`, `audit_logs` |
| [services/api/routers/notifications.py](../services/api/routers/notifications.py) | `GET /notifications`, read/all | - | - | `notifications` |

## 3) Lakehouse + Analytical Dependencies

| Layer | Producer | Path/Table | Consumer |
|---|---|---|---|
| Bronze (raw) | API ingest | MinIO `bronze/{tenant}/ingest_jobs/{job}/...` | Reprocess em ingest + stream processor |
| Silver (canonical events) | API ingest e jobs do stream processor | Kafka `canonical.*` | rules_engine, stream_processor (incremental features) |
| Gold (features snapshot) | stream_processor + jobs API | ClickHouse `betaml.player_features_daily` e MinIO `gold/tenant_id=...` | API (`players`/`feature-store`), ML service (`/score`) |

## 4) Frontend Route -> Backend Route (Critical Screens)

| Frontend page | Main backend dependencies |
|---|---|
| `/mappings` | `/mappings`, `/mappings/templates`, `/mappings/validate`, `/mappings/preview`, `/mappings/{id}/rollback` |
| `/ingest-jobs` | `/ingest/jobs`, `/ingest/jobs/{id}`, `/ingest/jobs/{id}/reprocess` |
| `/ingest-errors` | `/ingest/errors`, `/ingest/errors/{id}/resolve`, `/ingest/errors/{id}/replay` |
| `/alerts` + `/alerts/{id}` | `/alerts`, `/alerts/{id}`, `/alerts/{id}/triage`, `/alerts/{id}/label`, `/alerts/{id}/link-to-case` |
| `/cases` + `/cases/{id}` | `/cases`, `/cases/{id}`, `/cases/{id}/events`, `/cases/{id}/report-package*` |
| `/feature-store/{playerId}` | `/feature-store/players/{id}/history`, `/feature-store/players/{id}/current` |
| `/model-registry` | `/model-registry`, `/model-registry/{id}/ab-metrics`, `/model-registry/{id}/promote` |
| `/audit-logs` | `/audit-logs` |
| `/reports` | `/reports/monthly-summary`, `/reports/monthly-summary/csv` |
| `/admin`, `/settings` | `/admin/*`, `/scoring-config`, `/admin/flags`, `/admin/api-keys` |

## 5) Change Safety Checklist (Before touching critical flow)

- Se alterar ingest mapping/connectors, validar: `canonical.*`, `ingest.jobs`, `ingest_errors`, reprocess.
- Se alterar feature computation, validar: Redis online + `feature_snapshots` + ClickHouse `player_features_daily`.
- Se alterar DSL/rules scoring, validar: `scoring.alerts` + persistência em `alerts/rule_execution_logs`.
- Se alterar auto-case, validar consistência entre `rules_engine` e `alert_processor` (evitar dupla criação).
- Se alterar model promotion, validar `model_registry` + `model_inference_logs` + endpoints de explainability.
