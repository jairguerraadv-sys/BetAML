# BetAML – Hardening Plan

**Criado:** 2026-05-26  
**Última revisão:** 2026-05-26 (inspeção completa de código)  
**Base de inspeção:** branch `main`, commit mais recente  
**Objetivo:** mapear riscos remanescentes e ordenar os PRs de hardening  

---

## 1. Diagnóstico do Estado Atual

### 1.1 O que está funcionando e validado

| Área | Status |
|------|--------|
| Release gate remoto (`Release Readiness` run `25696032708`) | PASS |
| Alembic head aplicado (`20260526_000001_rls_runtime_hardening`) | OK |
| RLS ativo em 9 tabelas com `FORCE ROW LEVEL SECURITY` | OK/INCOMPLETO |
| PII: Fernet + HMAC + erasure LGPD | OK |
| Auth JWT com blacklist Redis + refresh rotation | OK |
| RBAC de 4 roles com permission matrix | OK |
| AuditLog com before/after JSONB + pii_accessed | OK |
| Secrets provider env/AWS/Azure em config.py | OK |
| 59+ arquivos de unit tests, threshold de coverage em 40% | OK/RISCO |
| 7 workflows CI/CD (ci, e2e, deploy-staging, release-readiness…) | OK |
| Helm charts para SaaS/on-prem | OK |
| `synthetic_bootstrap` gravado em `metrics` JSONB no ModelRegistry | OK/SEM GATE |

### 1.2 Achados concretos desta inspeção (2026-05-26)

#### Tabelas com RLS confirmado (migrations inspecionadas)

| Tabela | Migration | Observação |
|--------|-----------|------------|
| `tenants` | 20260519_000001 | SELECT policy com `current_tenant_id()` |
| `users` | 20260522_000006 + 20260526_000001 | 4 policies (SELECT/INSERT/UPDATE/DELETE) + auth_flow bypass |
| `system_flags` | 20260522_000007 | 4 policies CRUD |
| `api_keys` | 20260519_000001 | policy tenant_isolation |
| `ingest_errors` | 20260519_000001 | policy tenant_isolation |
| `external_validation_requests` | 20260519_000001 | policy tenant_isolation |
| `financial_transactions` | 20260526_000001 | USING + WITH CHECK |
| `bets` | 20260526_000001 | USING + WITH CHECK |
| `model_registry` | 20260526_000001 | USING + WITH CHECK |

#### Tabelas SEM RLS — expõem dados de tenant cruzado (CRÍTICO)

Verificado em `services/api/models.py` (31 modelos) vs. migrations de RLS:

| Tabela | Sensibilidade | Motivo de urgência |
|--------|---------------|--------------------|
| `players` | **PII CRÍTICA** | CPF/nome criptografados, cpf_hmac, pep_flag, renda |
| `device_events` | **PII ALTA** | device fingerprint, IP, geolocation |
| `player_kyc_events` | **PII ALTA** | eventos KYC, documentos |
| `alerts` | REGULATÓRIA | alertas PLD/FT por tenant |
| `cases` | REGULATÓRIA | casos de investigação por tenant |
| `case_events` | REGULATÓRIA | eventos de caso, auditoria |
| `report_packages` | REGULATÓRIA | pacotes COAF por tenant |
| `audit_logs` | COMPLIANCE | trilha de auditoria |
| `model_inference_logs` | OPERACIONAL | logs de inferência ML |
| `feature_snapshots` | OPERACIONAL | features calculadas por player |
| `scoring_configs` | OPERACIONAL | configuração de scoring |
| `notifications` | OPERACIONAL | notificações por tenant |
| `rule_definitions` | OPERACIONAL | regras PLD por tenant |
| `compound_rules` | OPERACIONAL | regras compostas |
| `rule_macros` | OPERACIONAL | macros de regras |
| `player_lists` | OPERACIONAL | listas negras/brancas |
| `player_list_entries` | OPERACIONAL | entradas nas listas |
| `rule_execution_logs` | AUDITORIA | logs de execução de regras |
| `mapping_configs` | OPERACIONAL | configurações de mapeamento |
| `ingest_jobs` | OPERACIONAL | jobs de ingestão |

#### ML: risco de promoção sintética confirmado

- `synthetic_bootstrap` é gravado em `metrics` JSONB no ModelRegistry (`services/ml_service/main.py:798`)
- A função `register_model_db` define `is_active=True` **sem checar** `synthetic_bootstrap`
- Resultado: modelo treinado com dados sintéticos pode ser promovido a champion silenciosamente
- `_allow_synthetic_training()` bloqueia apenas o *treinamento* quando `ALLOW_SYNTHETIC_SEED=false`, mas não bloqueia a *promoção* de um modelo já registrado com dados sintéticos

#### Framework de testes e fixtures

- **Framework:** `pytest` com `asyncio_mode = auto` (pytest-asyncio)
- **Threshold CI:** `--cov-fail-under=40` em `scripts/run_critical_unit_batches.sh`
- **Fixtures:** `tests/conftest.py` (root), `tests/unit/conftest.py`, `tests/unit/fixtures/`
- **Routers testados:** 59+ arquivos `test_*.py` em `tests/unit/`
- **Integração:** `tests/integration/` com 4 arquivos (ml_service_e2e, pipeline, stream_processor_e2e, new_endpoints)
- **Security tests:** `tests/security/` separados
- **E2E:** Playwright em `e2e/tests/`

#### Rotas FastAPI existentes

Routers em `services/api/routers/`: `admin`, `alerts`, `audit`, `auth`, `cases`, `compound_rules`, `external_validation`, `feature_store`, `health`, `ingest`, `internal`, `mappings`, `ml`, `notifications`, `player_lists`, `players`, `reports`, `rules`, `sanctions`, `search`, `stats` (21 routers)

#### Workflows GitHub Actions (7 confirmados)

| Workflow | Trigger | Jobs chave |
|----------|---------|------------|
| `ci.yml` | push/PR main | secret-hygiene, contributor-sanity, backend-tests (--cov-fail-under=40) |
| `e2e.yml` | push/PR main | Playwright smoke/extended |
| `release-readiness.yml` | workflow_dispatch | gate completo pré-produção |
| `deploy-staging.yml` | workflow_dispatch | deploy Helm staging |
| `external-validation-integration.yml` | schedule | validação externa |
| `capacity-smoke.yml` | schedule | Locust smoke |
| `data-quality.yml` | schedule | qualidade de dados |

### 1.3 Riscos identificados por categoria

#### CRÍTICO (bloqueia produção formal)

1. **Coverage threshold em 40%** — `scripts/run_critical_unit_batches.sh` usa `--cov-fail-under=40`. Limiar muito baixo para plataforma regulatória; não detecta regressões em rotas críticas.

2. **Bootstrap sintético sem gate de promoção** — **CONFIRMADO por inspeção de código.** `synthetic_bootstrap=True` é gravado em `metrics` JSONB (`services/ml_service/main.py:798`), mas `register_model_db` chama `is_active=True` **sem validar** esse campo. `_allow_synthetic_training()` bloqueia treinamento (quando `ALLOW_SYNTHETIC_SEED=false`), mas não bloqueia promoção de modelos já registrados com dados sintéticos. Modelos contaminados podem chegar a champion em staging/produção.

3. **ML champion/challenger sem feedback loop real** — `ModelRegistry` tem `is_active`/`is_challenger` mas não há evidência de circuito fechado: erro de inferência → feedback → re-treino. Promoter automático pode degradar silenciosamente.

4. **Secrets reais fora do repositório não verificados** — `.env.example` contém `devpass`, `minio123`, `admin123`, `superadmin123`. CI bloqueia esses valores em arquivos rastreados mas não verifica se chegaram a ambientes reais. Falta gate automático de rotação.

5. **RLS não cobre 20 tabelas sensíveis** — **CONFIRMADO por inspeção direta das migrations.** 9 tabelas têm RLS; 20 tabelas com `tenant_id` estão desprotegidas. As mais críticas: `players` (CPF/nome cifrados + cpf_hmac + pep_flag), `device_events` (fingerprint/IP/geo), `player_kyc_events`, `alerts`, `cases`, `case_events`, `report_packages`, `audit_logs`, `model_inference_logs`, `feature_snapshots`. Lista completa na seção 1.2.

#### ALTO (deve ser resolvido antes de carga real)

6. **Pipeline real incompleto: stream-processor publica direto em canonical** — Documentação anterior confirmou que o fluxo `raw → canonical → features → alerts` não é fiel ao runtime: o stream-processor atual publica majoritariamente direto em canonical. DLQ e backfill de eventos raw não têm cobertura de teste integrado.

7. **Contratos UI/API com lacunas** — Arquivos modificados no branch atual (`cases/[id]/page.tsx`, `reports/[id]/page.tsx`, `alerts/page.tsx`, etc.) sugerem ajustes de payload em andamento. Não há snapshot ou contrato tipado validado automaticamente end-to-end.

8. **Middleware de manutenção com cache de 60s pode mascarar estado** — `MaintenanceModeMiddleware` usa TTL de 60s no Redis. Uma janela de manutenção não propagada imediatamente pode deixar requests ativas durante operações críticas.

9. **`bandit` com B105, B107, B608 desabilitados** — B608 (SQL injection via formato de string dinâmico) está em `skips` com justificativa de allowlist. Essa justificativa precisa de revisão explícita a cada PR que toca queries.

10. **E2E sem fixture de tenant isolation cross-tenant** — Playwright testa fluxos por tenant mas não há teste automatizado que tente acessar dados de outro tenant com credencial válida do primeiro.

#### MÉDIO (hardening incremental)

11. **SLO/SLI sem alertas automáticos no ambiente-alvo** — `docs/slo-sli.md` define targets mas dashboards/alertas Grafana/Prometheus não foram validados com dados reais pós-deploy.

12. **Explainability de ML sem gate de governança** — `ModelRegistry.metrics` guarda métricas mas não há critério formalizado de `min_precision` ou `max_false_positive_rate` que bloqueie promoção automática.

13. **Trilha de auditoria sem teste de imutabilidade** — `AuditLog` não tem trigger/constraint de banco que impeça DELETE. Apenas RLS SELECT está testado; não há teste que confirme que um ADMIN não consegue apagar registros de auditoria.

14. **Ingress Helm sem TLS configurado por padrão** — `helm/betaml/templates/ingress.yaml` foi modificado recentemente; a configuração de TLS/cert-manager precisa ser validada nos values de staging/prod.

---

## 2. Riscos Prioritários (ordenados por impacto × probabilidade)

| # | Risco | Impacto | Probabilidade | PR alvo |
|---|-------|---------|---------------|---------|
| R1 | RLS incompleto em tabelas financeiras | CRÍTICO | MÉDIA | PR-01 |
| R2 | Coverage < 40% em rotas críticas sem detecção | CRÍTICO | ALTA | PR-02 |
| R3 | Bootstrap sintético contaminando modelo em prod | CRÍTICO | MÉDIA | PR-03 |
| R4 | Secrets padrão chegando a ambiente real | CRÍTICO | BAIXA | PR-04 |
| R5 | Pipeline Kafka direto em canonical sem DLQ testado | ALTO | ALTA | PR-05 |
| R6 | Contratos UI/API sem validação tipada E2E | ALTO | ALTA | PR-06 |
| R7 | ML sem feedback loop e gate de promoção | ALTO | MÉDIA | PR-07 |
| R8 | Auditoria apagável por admin privilegiado | MÉDIO | BAIXA | PR-08 |
| R9 | Cross-tenant sem teste automatizado | MÉDIO | BAIXA | PR-09 |
| R10 | B608 dinâmico sem revisão periódica | MÉDIO | BAIXA | PR-10 |

---

## 3. Ordem Recomendada dos PRs

### PR-01 – `hardening/rls-complete-tables`
**Status:** IMPLEMENTADO E VALIDADO LOCALMENTE (2026-05-26).
**Objetivo:** garantir RLS em todas as tabelas que contêm dados sensíveis de tenant.  
**Escopo:**
- Cobrir 20 tabelas sensíveis sem RLS com `ENABLE RLS` + `FORCE RLS` + policies por operação.
- Criar migração Alembic idempotente: `20260526_000002_rls_complete_sensitive_tables.py`.
- Adicionar suíte de regressão DB-first: `tests/security/test_rls_coverage.py`.
- Publicar matriz formal de cobertura: `docs/security/rls-coverage-matrix.md`.
- Agente recomendado: `BetAML Security and PII Agent`

**Critério de aceite:** tabelas sensíveis sem RLS foram cobertas; testes de isolamento/catálogo passam; sem enfraquecimento das policies já existentes.

---

### PR-02 – `hardening/coverage-threshold`
**Objetivo:** elevar threshold de coverage de 40% para 70% em rotas críticas.  
**Escopo:**
- Aumentar `--cov-fail-under` para 70 no batch crítico (cases, alerts, auth, ingest).
- Adicionar módulos faltantes ao `--cov` source (rules_engine, stream_processor stubs).
- Mapear quais routers têm < 50% de coverage com `--cov-report=term-missing`.
- Agente recomendado: `BetAML ML Hardening Agent` (para ML) + direto para outros módulos.

**Critério de aceite:** `pytest ... --cov-fail-under=70` passa no CI sem falsos positivos.

---

### PR-03 – `hardening/ml-no-synthetic-seed`
**Objetivo:** remover ou isolar bootstrap sintético do fluxo de treinamento.  
**Escopo:**
- Gate no trainer: se `ALLOW_SYNTHETIC_SEED=false`, bloquear promoção de modelo treinado sobre dados sintéticos.
- Adicionar campo `trained_on_synthetic` ao `ModelRegistry`.
- `is_active` não pode ser `true` se `trained_on_synthetic=true` em ambiente `production`.
- Teste de regressão: treino com flag sintético não promove champion.
- Agente recomendado: `BetAML ML Hardening Agent`

**Critério de aceite:** tentativa de promover modelo sintético em `ENVIRONMENT=production` retorna 4xx.

---

### PR-04 – `hardening/secrets-scan-gate`
**Status:** IMPLEMENTADO E VALIDADO LOCALMENTE (2026-05-26).
**Objetivo:** bloquear credenciais padrão em qualquer arquivo rastreado e validar rotação.  
**Escopo:**
- Expandir job `secret-hygiene` no CI para cobrir `values*.yaml` e `configmap.yaml` do Helm.
- Adicionar checklist de rotação ao `docs/security-secrets-management.md`.
- Verificar que `GF_SECURITY_ADMIN_PASSWORD=admin123` não está em `values.yaml` de staging/prod.
- Agente recomendado: `BetAML Security and PII Agent`

**Critério de aceite:** commit com `devpass` ou `admin123` em qualquer arquivo Helm é rejeitado por CI.

**Risco residual após PR-04:** scanner bloqueia defaults inseguros em arquivos rastreados sensíveis (helm/workflows/deploy), porém ainda não comprova rotação efetiva em ambientes reais. Follow-up: validar secrets manager + Kubernetes secrets em staging.

---

### PR-05 – `hardening/pipeline-real-dlq`
**Objetivo:** validar que o pipeline Kafka tem DLQ funcional e backfill testado.  
**Escopo:**
- Mapear tópicos reais no stream-processor vs. tópicos declarados em `ingest-contract.md`.
- Testar cenário de mensagem malformada → DLQ → replay.
- Confirmar que `DLQ_MAX_RETRIES=3` está sendo respeitado no consumer.
- Agente recomendado: `BetAML Real Pipeline Agent`

**Critério de aceite:** mensagem inválida vai ao DLQ; replay processa sem duplicata; teste de integração verde.

---

### PR-06 – `hardening/ui-api-contracts`
**Objetivo:** garantir que contratos entre frontend e API estão alinhados nos arquivos modificados.  
**Escopo:**
- Validar payloads de `cases/[id]`, `reports/[id]`, `alerts`, `cases/new` contra OpenAPI spec.
- Corrigir divergências encontradas (campos opcionais vs. obrigatórios, rotas quebradas).
- Agente recomendado: `BetAML UI API Contract Agent`

**Critério de aceite:** `tsc --noEmit` passa; nenhuma rota 404 nos flows críticos do smoke E2E.

---

### PR-07 – `hardening/ml-feedback-loop`
**Objetivo:** fechar o ciclo de feedback do ML para evitar degradação silenciosa.  
**Escopo:**
- Adicionar `min_precision` e `max_false_positive_rate` como campos obrigatórios ao `ScoringConfig`.
- Gate de promoção: modelo não pode ser promovido se métricas estão abaixo do threshold.
- Registrar no AuditLog toda promoção/rebaixamento de modelo.
- Agente recomendado: `BetAML ML Hardening Agent`

**Critério de aceite:** promoção de modelo com precision < threshold falha com 422; auditlog registra tentativa.

---

### PR-08 – `hardening/audit-immutability`
**Objetivo:** garantir que logs de auditoria não podem ser apagados.  
**Escopo:**
- Adicionar trigger PostgreSQL em `audit_logs`: `BEFORE DELETE RAISE EXCEPTION`.
- Ou: revogar `DELETE` privilege do role `betaml_app` na tabela `audit_logs`.
- Adicionar teste que confirma DELETE retorna erro mesmo com SUPER_ADMIN.
- Agente recomendado: `BetAML Security and PII Agent`

**Critério de aceite:** `DELETE FROM audit_logs WHERE id = ...` retorna erro; teste de regressão verde.

---

### PR-09 – `hardening/cross-tenant-e2e`
**Objetivo:** teste automatizado de isolamento cross-tenant.  
**Escopo:**
- Playwright: criar tenant A e tenant B; autenticar como usuário do A; tentar acessar recurso do B via API direta; confirmar 403/404.
- Cobrir: players, cases, alerts, report_packages.
- Agente recomendado: `BetAML Security and PII Agent`

**Critério de aceite:** suite Playwright `security` passa com todos os cross-tenant checks verdes.

---

### PR-10 – `hardening/bandit-b608-review`
**Status:** IMPLEMENTADO LOCALMENTE, validação CI pendente.
**Objetivo:** revisar e documentar formalmente cada uso de SQL dinâmico que justifica B608 skip.  
**Escopo:**
- Listar todos os usos de `text()` e `execute()` com strings dinâmicas.
- Confirmar que cada um usa parâmetros bind (`:param`) e não formatação de string.
- Remover skip de B608 ou adicionar inline `# nosec B608 – [justificativa explícita]`.
- Agente recomendado: `BetAML Security and PII Agent`

**Critério de aceite:** sem `B608` genérico em `pyproject.toml`; cada ocorrência tem `nosec` com justificativa.

---

## 4. Dependências entre Etapas

```
PR-01 (RLS) ──────────────────────┐
PR-04 (Secrets) ──────────────────┤
                                   ▼
PR-02 (Coverage) ─────────────► PR-09 (Cross-tenant E2E)
                                   ▲
PR-03 (ML no-synthetic) ──────────┤
PR-07 (ML feedback loop) ─────────┘

PR-05 (Pipeline/DLQ) ──── independente, mas bloqueia observabilidade real
PR-06 (UI/API contracts) ── pode ser paralelo com PR-01 e PR-02
PR-08 (Audit immutability) ── depois de PR-01 (RLS base estável)
PR-10 (Bandit B608) ──── pode ser feito a qualquer momento, não bloqueia
```

**Sequência mínima para produção formal:**

```
PR-04 → PR-01 → PR-02 → PR-05 → PR-06 → validação de staging
```

**Sequência completa de hardening:**

```
(PR-04, PR-10 em paralelo)
→ PR-01
→ (PR-02, PR-03, PR-05, PR-06 em paralelo)
→ PR-07
→ (PR-08, PR-09 em paralelo)
→ release candidate final
```

---

## 5. Comandos Locais para Validação

### Ambiente

```bash
# Subir stack completa
cd infra && docker compose up -d

# Aplicar migrations
cd services/api && alembic upgrade head

# Verificar cabeça atual
cd services/api && alembic current
```

### Testes unitários com coverage

```bash
# Batch crítico com threshold atual (40%)
bash scripts/run_critical_unit_batches.sh --include-remainder

# Rodar com threshold maior (validação local do PR-02)
cd services/api
pytest tests/unit -v \
  --cov=services/api \
  --cov-report=term-missing \
  --cov-fail-under=70

# Gerar relatório HTML
pytest tests/unit --cov=services/api --cov-report=html:htmlcov
open htmlcov/index.html
```

### Verificar RLS sem contexto de tenant (PR-01)

```bash
# Conectar no banco e testar isolamento
psql "$DATABASE_URL" -c "
  SELECT set_config('app.current_tenant', '', false);
  SELECT count(*) FROM financial_transactions;
  -- Esperado: 0 ou ERROR dependendo da política
"
```

### Scan de secrets em arquivos Helm

```bash
grep -rn "devpass\|minio123\|admin123\|superadmin123\|changeme" helm/ .env* || echo "Clean"
```

### Security scan (Bandit)

```bash
cd services/api
bandit -r . --skip B105,B107,B110,B112,B311,B405 \
  --exclude ./alembic,./seeds.py \
  -ll  # only MEDIUM+ severity
```

### TypeScript check (frontend)

```bash
cd services/frontend
npx tsc --noEmit
```

### Smoke E2E

```bash
cd e2e
npx playwright test --project=smoke
```

### Validar idempotência de migration

```bash
bash scripts/check_migrations_idempotent.sh  # ou equivalente local
# Alternativa manual:
cd services/api
alembic upgrade head && alembic upgrade head  # segundo run deve ser no-op
```

### Verificar cobertura por arquivo

```bash
cd services/api
pytest tests/unit \
  --cov=routers/cases \
  --cov=routers/alerts \
  --cov=routers/auth \
  --cov-report=term-missing 2>&1 | grep -E "TOTAL|routers/"
```

---

## 6. Lacunas que Precisam de Investigação

| # | Lacuna | Como investigar | Urgência |
|---|--------|-----------------|----------|
| L1 | ~~Quais tabelas têm RLS e quais não têm~~ | **CONFIRMADO:** 9 têm RLS, 20 não têm — ver seção 1.2 | RESOLVIDA |
| L2 | Coverage real por router (não só agregada) | `pytest ... --cov-report=term-missing` focado em `routers/` | IMEDIATA |
| L3 | Stream-processor: tópicos reais vs. contrato | Comparar `services/stream_processor/main.py` com `docs/ingest-contract.md` | ALTA |
| L4 | Se `betaml_app` DB role tem `DELETE` em `audit_logs` | `\dp audit_logs` no psql | ALTA |
| L5 | ~~Se promoção de modelo tem gate de métricas ou é manual~~ | **CONFIRMADO:** `register_model_db` não verifica `synthetic_bootstrap` antes de `is_active=True` — bug real | RESOLVIDA (é bug, vira PR-03) |
| L6 | Grafana/Prometheus provisionados com dados reais | Deploy em staging e verificar dashboards | MÉDIA |
| L7 | `values-staging.yaml` não contém credentials de dev | `grep -n "devpass\|admin123" helm/betaml/values-staging.yaml` | IMEDIATA |
| L8 | ~~Se `ALLOW_SYNTHETIC_SEED` é respeitado no trainer~~ | **CONFIRMADO:** flag respeita treinamento mas não a promoção — gap real em `register_model_db` | RESOLVIDA (vira PR-03) |
| L9 | Se ingress em staging tem TLS ativo | `kubectl describe ingress -n betaml` em staging | ALTA |
| L10 | Quais queries usam `text()` com formatação dinâmica | `grep -rn "text(f\|text(\".*{" services/api/` | ALTA |

---

## 7. Referências

- `docs/product-readiness-backlog.md` — P0/P1/P2 residuais pós-readiness
- `docs/auditoria-consolidada-pld-2026-03-20.md` — contexto histórico e invalidações
- `docs/go-live-checklist.md` — critérios formais de go/no-go
- `docs/security-secrets-management.md` — gestão de secrets (env/AWS/Azure)
- `docs/ops-guide.md` — runbooks operacionais
- `artifacts/readiness/` — evidências objetivas do estado validado localmente
- `services/api/alembic/versions/` — histórico de migrations
- `.github/workflows/ci.yml` — pipeline CI com jobs de segurança e testes
