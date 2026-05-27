# RLS Coverage Matrix — PR-01 hardening/rls-complete-tables

## Resumo Executivo

- Antes do PR-01: 9 tabelas com RLS/FORCE RLS e 20 tabelas sensíveis tenant-scoped sem cobertura completa.
- Depois do PR-01: as 20 tabelas sensíveis mapeadas no hardening plan recebem `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + policies por operação.
- Padrão aplicado: `current_tenant_id()` (função SQL já padronizada nas migrations existentes), sem bypass novo para `SUPER_ADMIN`.

Migration aplicada neste PR:

- `services/api/alembic/versions/20260526_000002_rls_complete_sensitive_tables.py`

## Tenant Context e Bypass Controlado

Tenant context padrão do projeto:

- `current_tenant_id()`
- implementação:
  - `NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID`

Bypass controlado já existente (não introduzido neste PR):

- `users` SELECT permite `current_setting('app.auth_flow', TRUE) IN ('login', 'refresh')` para autenticação sem quebrar isolamento normal.
- `tenants` policy existente permite bootstrap quando `current_tenant_id() IS NULL` (setup/seed/migration), sem abrir leitura cross-tenant em runtime autenticado.

## Matriz de Cobertura

| Tabela | Sensibilidade | Tem tenant_id direto | Tenant via FK | RLS antes | FORCE antes | RLS depois | FORCE depois | SELECT policy | INSERT policy | UPDATE policy | DELETE policy | Observação |
|--------|---------------|---------------------|---------------|-----------|-------------|------------|--------------|---------------|---------------|---------------|---------------|------------|
| players | PII CRÍTICA | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | CPF/nome cifrados + cpf_hmac + pep_flag/renda |
| device_events | PII ALTA | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | fingerprint/IP/geo |
| player_kyc_events | PII ALTA | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | player_id é TEXT, isolamento segue por tenant_id |
| alerts | REGULATÓRIA | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Alertas PLD/FT por tenant |
| cases | REGULATÓRIA | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Casos investigativos |
| case_events | REGULATÓRIA | Sim | case_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Tem FK para `cases`, mas usa tenant_id direto |
| report_packages | REGULATÓRIA | Sim | case_id/player_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Cadeia de custódia/coaf protocol |
| audit_logs | COMPLIANCE | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING false | USING false | Imutabilidade forte segue no PR-08; trigger já existente em migração anterior |
| model_inference_logs | OPERACIONAL | Sim | player_id/model_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING false | USING false | Log append-only de inferência |
| feature_snapshots | OPERACIONAL | Sim | player_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Features/snapshot por player |
| scoring_configs | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | 1 config por tenant (unique tenant_id) |
| notifications | OPERACIONAL | Sim | user_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Notificações de alerta/caso |
| rule_definitions | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Estratégia de detecção sensível |
| compound_rules | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Regras compostas |
| rule_macros | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Macros DSL |
| player_lists | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Listas por tenant |
| player_list_entries | OPERACIONAL | Sim | list_id/player_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Tenant direto, sem necessidade de EXISTS via FK |
| rule_execution_logs | AUDITORIA | Sim | rule_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING false | USING false | Log append-only |
| mapping_configs | OPERACIONAL | Sim | N/A | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Config de mapeamento ingestão |
| ingest_jobs | OPERACIONAL | Sim | mapping_config_id (opcional) | Não | Não | Sim | Sim | tenant_id = current_tenant_id() | WITH CHECK tenant_id = current_tenant_id() | USING+WITH CHECK tenant_id = current_tenant_id() | USING tenant_id = current_tenant_id() | Jobs e backfill |

## Tabelas Globais/Justificadas

| Tabela | Sensibilidade | Justificativa |
|--------|---------------|---------------|
| tenants | GLOBAL/JUSTIFICADA | tabela base de cadastro de tenants; já possui RLS/FORCE com policy própria em migration anterior |

## Como Testar Manualmente

```bash
psql "$DATABASE_URL" -c "
  SELECT set_config('app.current_tenant', '', false);
  SELECT count(*) FROM players;
"
```

Resultado esperado: `0` (ou erro seguro por policy), nunca dados cross-tenant.

## Como Validar via Pytest

```bash
cd services/api
pytest tests/security/test_rls_coverage.py -q
```

Obs: os testes de banco dessa suíte exigem stack local e `TEST_STACK_UP=1`.

## Limitações e Próximos PRs

### Nota sobre BYPASSRLS

Parte dos testes runtime de isolamento em `tests/security/test_rls_coverage.py` é automaticamente marcada como skip quando a role de conexão possui `BYPASSRLS`, para evitar falso positivo/falso negativo em ambiente local privilegiado.

Antes de considerar este hardening como validado para produção, a suíte deve rodar em CI/staging com uma role equivalente à role runtime da aplicação, sem `BYPASSRLS`.

A introspecção de catálogo continua validando que tabelas sensíveis permanecem com `relrowsecurity=true` e `relforcerowsecurity=true`.

- PR-08 (`hardening/audit-immutability`): reforçar imutabilidade anti-DELETE/UPDATE em `audit_logs` com hardening dedicado de compliance.
- PR-09 (`hardening/cross-tenant-e2e`): validação E2E Playwright de isolamento cross-tenant em fluxos críticos de UI/API.
- Se surgir tabela tenant-scoped sem `tenant_id` direto, adicionar policies com `EXISTS (...)` via FK e testes específicos.
