Você é um arquiteto de software + engenheiro full‑stack sênior. Construa um produto SaaS multi‑tenant chamado BetAML: um sistema de PLD/FT “nível banco” para operadores de apostas de quota fixa no Brasil, projetado para BIG DATA (alto volume, alta taxa de eventos, histórico longo, auditoria forte).

OBJETIVO DO PRODUTO
1) Entrada universal (conectores/adapters) capaz de receber dados heterogêneos de múltiplos backoffices e converter para um modelo canônico.
2) Motor de risco não engessado: regras parametrizáveis por tenant + análise relativa ao perfil financeiro e transacional do jogador (baseline, desvios, peer group).
3) ML: anomalias e priorização de alertas, com governança (versão, métricas, explicabilidade e auditoria).
4) Case management: alertas → investigação → evidências → decisão → geração de “ReportPackage” (payload JSON) para reporte (sem integração real com sistemas externos no MVP).
5) Enterprise-ready: multi-tenant, isolamento lógico, RBAC, auditoria, observabilidade, idempotência, escalabilidade horizontal.

========================================================
1) ARQUITETURA BIG DATA (OBRIGATÓRIA)
========================================================

A arquitetura deve ser orientada a eventos (event-driven) e separada em camadas:

(A) API Layer (FastAPI)
- Faz autenticação/autorizações, CRUD de regras/casos/configs e expõe OpenAPI.
- Ingestão (arquivo/API) NÃO processa pesado: validação + persistência mínima + publicação em barramento.
- Não faz ETL grande, nem treino ML. Apenas enfileira/publica.

(B) Event Bus (Kafka API)
- Use Kafka (em produção) e uma alternativa leve para dev (ex.: Redpanda) via Docker Compose.
- Tópicos por domínio:
  - raw.players
  - raw.transactions
  - raw.bets
  - raw.device_events
  - canonical.players
  - canonical.transactions
  - canonical.bets
  - canonical.device_events
  - features.player_daily
  - scoring.alerts
  - cases.events (eventos de caso/auditoria)
- Use esquema versionado e registro de schemas (Schema Registry ou alternativa). Tudo versionado por `schemaVersion`.

(C) Data Lakehouse (Histórico bruto + canônico)
- Use MinIO (S3 compatível) no dev e S3/GCS/Azure no prod.
- Armazenamento em parquet particionado por:
  - tenant_id
  - event_date (YYYY-MM-DD)
  - entity_type
  - source_system
- Camadas:
  - Bronze: raw_payloads como chegaram (com metadados)
  - Silver: canônico normalizado
  - Gold: agregados e features (ex.: por dia, por semana)

(D) OLAP para consultas operacionais (dashboards e investigações)
- Use ClickHouse (ou alternativa OLAP) para queries rápidas de:
  - alertas por período
  - top jogadores por risco
  - séries temporais
  - drill-down de transações/apostas
- Frontend deve consultar APIs que usam OLAP para listagens.

(E) OLTP para entidades de workflow e governança
- Postgres fica para: tenants, users, RBAC, RuleDefinition, MappingConfig, Cases, CaseEvents, AuditLog, ReportPackages.
- Não use Postgres como data store principal de eventos de alto volume (transações/apostas).

(F) Stream Processing / Feature Computation
- Um serviço “stream-processor” (Python) consumindo tópicos canônicos:
  - calcula features em janelas (24h/7d/30d), baseline incremental, correlações (device/shared accounts)
  - grava features no lakehouse (Gold) e no OLAP (ClickHouse) para consulta rápida
  - publica eventos de “feature-updated” e “candidate-alert” em tópicos

(G) Rules Engine Service (stream)
- Serviço independente, consumindo:
  - canonical.transactions / canonical.bets + features
- Avalia regras ativas do tenant (DSL) e emite:
  - Alert (scoring.alerts) + RuleExecutionLog (para auditoria)
- Deve suportar:
  - regras absolutas
  - regras relativas ao baseline do jogador
  - regras por peer group

(H) ML Services
- ml-train (batch): treina por tenant usando offline store (lakehouse Gold/Silver)
- ml-score (online): recebe features e retorna anomalyScore 0–1 + top drivers
- Use versionamento de modelos por tenant (model_registry) e rastreio de métricas.
- Armazene artefatos do modelo no lakehouse (ex.: S3/MinIO).

(I) Orquestração batch (opcional no MVP, mas deixe esqueleto)
- Prefect/Airflow para pipelines de:
  - backfill
  - treino diário/semanal
  - rebuild de baselines
  - compactação de dados / retenção

(J) Observabilidade
- Logs estruturados em todos os serviços
- Métricas Prometheus (opcional no MVP) e health checks por serviço
- Tracing (opcional) com correlação por request_id e event_id

========================================================
2) MONOREPO E ENTREGÁVEIS
========================================================

Crie um monorepo com:
- /services/api                 (FastAPI)
- /services/stream_processor    (consumer + features)
- /services/rules_engine        (consumer + DSL)
- /services/ml_service          (FastAPI scoring + training endpoints)
- /services/frontend            (Next.js)
- /infra                        (docker-compose, configs, init scripts)
- /libs                         (schemas, dsl parser, shared models, clients)

Entregue:
- Código fonte completo + instruções para rodar local
- Docker Compose com: postgres, redis, minio, clickhouse, kafka/redpanda, schema-registry (se possível), api, stream_processor, rules_engine, ml_service, frontend
- Seeds (2 tenants, 3 users/tenant, dados sintéticos + cenários suspeitos)
- Testes mínimos (unit e integração): ingestão → evento → canônico → regra → alerta → caso
- Documentação: README + OpenAPI/Swagger

========================================================
3) MULTI-TENANT, SEGURANÇA E LGPD
========================================================

- tenant_id sempre derivado do token; nunca aceitar tenant_id do client.
- RBAC: ADMIN, AML_ANALYST, AUDITOR.
- PII (CPF, nome, endereço):
  - mascaramento no frontend (ex.: mostrar só últimos 3 dígitos por padrão)
  - criptografia em repouso para colunas sensíveis no OLTP (Postgres) e controle de acesso.
- Auditoria completa:
  - toda mudança em RuleDefinition, MappingConfig, status de Alert/Case, geração de ReportPackage gera AuditLog.
- Data retention:
  - raw_payloads no lakehouse com política de retenção por tenant (configurável).
- Idempotência:
  - todo evento tem (tenant_id, source_system, source_event_id) e deve ser deduplicado na camada canônica.

========================================================
4) MODELO DE DADOS (CANÔNICO + WORKFLOW)
========================================================

Defina um “Canonical Event Envelope” (para Kafka e lakehouse):
{
  eventId: uuid,
  tenantId: uuid,
  sourceSystem: string,
  sourceEventId: string,
  schemaVersion: int,
  entityType: "PLAYER"|"TRANSACTION"|"BET"|"DEVICE_EVENT",
  occurredAt: timestamp,
  payload: {...},         // canônico (Silver)
  rawPayload: {...},      // bruto (Bronze)
  ingestMetadata: { receivedAt, fileName?, apiKeyId?, checksum?, mapperVersion }
}

Payload canônico mínimo:

PLAYER:
- externalPlayerId, cpf, name, birthDate, pepFlag, declaredIncomeMonthly?, profession?

TRANSACTION:
- externalTransactionId?
- playerCpf/playerId
- type (DEPOSIT/WITHDRAWAL/CHARGEBACK/BONUS/ADJUSTMENT)
- amount, currency=BRL
- method (PIX/TED/CARD/WALLET/OTHER)
- status (PENDING/SETTLED/FAILED/REVERSED)
- paymentInstrument (institutionCode, holderDocument, verifiedFlag)
- occurredAt

BET:
- externalBetId?
- playerCpf/playerId
- stakeAmount, odds, potentialPayout, settledPayout
- marketType, sport, eventId, selection
- channel (WEB/APP/TERMINAL)
- placedAt, settledAt?

DEVICE_EVENT:
- deviceId, ip, geoCountry, userAgent, occurredAt

Workflow (OLTP em Postgres):
- Tenants, Users, MappingConfig, RuleDefinition
- Alerts, Cases, CaseEvents/Evidence, ReportPackages, AuditLog

========================================================
5) ENTRADA UNIVERSAL (INGESTÃO)
========================================================

Implemente dois modos:

(A) Upload de arquivo
- POST /ingest/file
- Recebe CSV/JSON + headers: sourceSystem, mappingConfigId
- API:
  - valida arquivo
  - cria um “IngestJob” no Postgres (status QUEUED)
  - envia mensagem para Kafka: ingest.jobs
- Worker/processor:
  - lê o arquivo, aplica MappingConfig (transforms: parseDate, normalizeCpf, mapEnum, coerceDecimal)
  - produz eventos para tópicos raw.* e depois canonical.*
  - escreve Bronze/Silver no lakehouse

(B) API de eventos
- POST /ingest/event (um)
- POST /ingest/batch (lista)
- A API valida e publica em raw.* e canonical.* via producer.
- Não transforma pesado aqui; apenas valida + aplica mapping leve se necessário.

Crie 2 conectores fictícios (schemas diferentes):
- BackofficeAlpha
- BackofficeBeta
Ambos mapeiam para o canônico via MappingConfig versionado.

========================================================
6) FEATURE STORE E PERFIL DO JOGADOR
========================================================

Crie um conjunto de features mínimas e uma estratégia de storage:

Online features (para scoring rápido):
- Redis (key: tenant:playerId:features) com TTL e versionamento

Offline features (para treino e auditoria):
- lakehouse Gold (parquet), particionado por tenant e data

Features mínimas (player):
- deposit_sum_24h, deposit_sum_7d, deposit_sum_30d
- deposit_count_24h, deposit_count_7d
- withdrawal_sum_24h, withdrawal_sum_7d
- bet_stake_sum_24h, bet_stake_sum_7d
- ratio_withdrawal_to_deposit_7d
- baseline_avg_daily_deposit, baseline_stddev_deposit
- zscore_current_deposit_vs_baseline
- new_payment_instrument_flag
- new_device_flag
- shared_device_count (quantos CPFs no mesmo device)
- shared_bank_account_count (quantos players por holderDocument)

========================================================
7) RULES ENGINE (NÃO ENGESSADO)
========================================================

Implemente RuleDefinition com:
- status, severity, scope
- condition_dsl (texto)
- params (json)
- versionamento

DSL (parser próprio simples):
- operadores: > < >= <= == != in contains and or not
- funções: sum(window), count(window), zscore(value, baseline), ratio(a,b)
- acesso a:
  - campos do evento (transaction.amount, bet.stakeAmount, etc.)
  - features (features.deposit_sum_24h, features.zscore..., etc.)
  - atributos do jogador (player.pepFlag, player.declaredIncomeMonthly, etc.)

Regras default (mínimo 12):
1) Spike vs baseline (zscore alto)
2) Muitos depósitos pequenos em 24h (structuring)
3) Saque rápido após depósito (tempo curto)
4) Instrumento novo + valor alto
5) PEP com desvio alto
6) Mesma conta bancária usada por múltiplos players
7) Mesmo deviceId em múltiplos CPFs
8) Alta razão saque/depósito 7d
9) Apostas com stake elevando abruptamente em 7d
10) Reversões/chargebacks acima do normal
11) Múltiplas tentativas falhas de depósito + sucesso subsequente grande
12) Padrão de “round-tripping” (depósito → aposta mínima → saque)

Cada match:
- gera Alert (com evidências: features, thresholds, rule version)
- escreve RuleExecutionLog para auditoria
- publica scoring.alerts

========================================================
8) ML (BIG DATA FRIENDLY)
========================================================

- Treino offline por tenant (diário):
  - lê features Gold do lakehouse (últimos 90 dias)
  - treina modelo de anomalia (IsolationForest/LOF/etc.)
  - salva artefato versionado no lakehouse
  - registra no model_registry (Postgres): modelVersion, trainedAt, datasetWindow, métricas

- Scoring online:
  - rules_engine ou stream_processor chama ml_service (ou faz request assíncrono)
  - retorna anomalyScore e top_drivers
  - atualiza Alert (tipo ANOMALY ou COMPOSITE)

- Explicabilidade:
  - ao menos: lista de features com maiores desvios e contribuição (heurística)
  - armazenar no Alert.evidence

========================================================
9) CASE MANAGEMENT E REPORT PACKAGE
========================================================

- Cases ficam no Postgres.
- Um Case pode agrupar múltiplos Alerts do mesmo player.
- Auto-criação:
  - severity >= HIGH
  - ou riskScore >= threshold do tenant
  - ou correlação de múltiplos alertas em X horas

ReportPackage:
- gerar payload JSON com:
  - dados do jogador (mascarar na UI, completo no payload)
  - lista de eventos relevantes (transactions/bets) com timestamps e valores
  - regras que dispararam e evidências
  - justificativa do analista
- export JSON e CSV; sem submissão externa.

========================================================
10) FRONTEND ENTERPRISE (NEXT.JS)
========================================================

Páginas:
1) Login
2) Dashboard (KPIs, séries, top players)
3) Alertas (grid com filtros, paginação server-side, detalhes com evidências)
4) Casos (lista, detalhe, timeline, notas, upload evidências, gerar report package)
5) Regras (CRUD, editor DSL com validação e simulação)
6) MappingConfig (CRUD, testar mapping com amostra)

Requisitos:
- paginação server-side
- filtros avançados (por severidade, status, período, player, rule)
- controle por role (AUDITOR só leitura e acesso a AuditLog)

========================================================
11) APIs OBRIGATÓRIAS (FASTAPI)
========================================================

Auth:
- POST /auth/login
- POST /auth/refresh
- POST /auth/logout
- GET /me

Ingest:
- POST /ingest/file
- POST /ingest/event
- POST /ingest/batch
- GET  /ingest/jobs

Rules:
- CRUD /rules
- POST /rules/{id}/simulate (simula DSL em um conjunto de eventos de teste)

Alerts:
- GET /alerts (filtros, paginação)
- GET /alerts/{id}
- POST /alerts/{id}/triage
- POST /alerts/{id}/close
- POST /alerts/{id}/link-to-case

Cases:
- CRUD /cases
- POST /cases/{id}/assign
- POST /cases/{id}/events (nota/decisão)
- POST /cases/{id}/evidence (upload)
- POST /cases/{id}/report-package (gera payload)

Audit:
- GET /audit-logs (somente AUDITOR/ADMIN)

========================================================
12) INFRA LOCAL (DOCKER COMPOSE) + SEED
========================================================

Subir localmente:
- postgres
- redis
- minio
- clickhouse
- kafka/redpanda
- (schema registry se viável)
- api
- stream_processor
- rules_engine
- ml_service
- frontend

Seeds:
- 2 tenants: OperadorA, OperadorB
- 3 users/tenant: ADMIN, AML_ANALYST, AUDITOR
- Dados sintéticos (50 players/tenant) + cenários suspeitos:
  - structuring
  - spike de stake
  - múltiplos CPFs no mesmo device
  - saque rápido pós depósito
  - conta bancária compartilhada

========================================================
13) TESTES E QUALIDADE
========================================================

- Testes unitários:
  - parser DSL
  - transforms do mapping
- Testes de integração (docker):
  - ingest/file → kafka → canônico → features → rules → alert → case
- Garantir idempotência e isolamento tenant

========================================================
14) COMO VOCÊ DEVE RESPONDER
========================================================

1) Primeiro escreva um plano curto (10–15 bullets).
2) Depois gere o repositório completo com todos os arquivos necessários (sem TODOs críticos).
3) Se faltar alguma decisão, faça no máximo 5 perguntas objetivas; se eu não responder, assuma padrões razoáveis.

CONFIGS (DEV)
- PROJECT_NAME=betaml
- JWT_SECRET=dev-secret-change-me
- POSTGRES_PASSWORD=devpass
- REDIS_PASSWORD=devpass
- MINIO_ACCESS_KEY=minio
- MINIO_SECRET_KEY=minio123
- CLICKHOUSE_USER=default
- CLICKHOUSE_PASSWORD=

AGORA: gere o repositório completo seguindo este prompt.