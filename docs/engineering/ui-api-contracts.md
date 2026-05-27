# UI API Contracts - PR-06

Atualizado em: 2026-05-27
Status: implementado localmente, validacao CI pendente

## Politica API-first

- A API FastAPI e a fonte de verdade do contrato.
- O OpenAPI e gerado a partir dos schemas reais dos endpoints.
- O frontend consome tipos sincronizados a partir do OpenAPI para fluxos criticos.
- Mudancas de contrato devem ser acompanhadas por:
  - atualizacao de tipos gerados,
  - typecheck frontend,
  - testes de contrato backend.

## Como exportar OpenAPI

```bash
python scripts/export_openapi.py --output artifacts/openapi/openapi.json
```

Propriedades do export:

- JSON deterministico (`sort_keys=true`).
- Falha em erro de carga do app/schema.
- Nao depende de stack externa.

## Como gerar tipos TS

```bash
cd services/frontend
npm run generate:api-types
```

Arquivo gerado:

- `services/frontend/lib/generated/api-types.ts`

## Matriz de contratos criticos

| Tela/Componente | Endpoint | Metodo | Request TS | Response TS | Schema backend | Status | Observacao |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cases list | /cases | GET | params `{status_filter, limit, offset}` | `Case[]` | `CaseSummaryOut[]` | OK | response_model explicito no backend |
| case detail | /cases/{case_id} | GET | path `case_id` | `CaseDetail` | `CaseDetailOut` | OK | timeline/evidence/report_packages tipados |
| cases new | /cases | POST | `CaseCreate` | `{id,title,status,reference_number}` | `CaseCreate`/`CaseCreateOut` | OK | rota de criacao alinhada ao form |
| alerts list | /alerts | GET | params `{status, per_page}` | `{total, next_cursor, items}` | `AlertsListOut` | OK | pagina usa `data.items` com fallback |
| alert detail | /alerts/{alert_id} | GET | path `alert_id` | `AlertDetail` | `AlertDetailOut` | OK | campos opcionais tratados no frontend |
| reports dashboard | /reports/monthly-summary | GET | `date_from,date_to` | `MonthlyReport` | `MonthlyReportOut` | OK | summary tipado no backend |
| report package list | /report-packages | GET | params `{limit,status}` | `ReportPackage[]` | `ReportPackageListItemOut[]` | OK | usado para listagem e fila |
| report package detail | /report-packages/{rp_id} | GET | path `rp_id` | `ReportPackageDetail` | `ReportPackageDetailOut` | NEEDS_BACKEND_FIX -> OK | adicionado endpoint dedicado (antes havia lookup em lista) |
| report package download | /report-packages/{rp_id}/download | GET | path `rp_id` | `Blob` | binary/json | OK | fluxo de download preservado |

## Divergencias encontradas e corrigidas

- `FIELD_NAME_MISMATCH/SHAPE`: detalhe de dossie em `/reports/[id]` buscava `/report-packages` e filtrava client-side.
  - Correcao: novo endpoint `/report-packages/{rp_id}` + client `fetchReportPackage`.
- `MISSING_RESPONSE_MODEL`: endpoints criticos sem `response_model` explicito.
  - Correcao: contratos de saida adicionados em `cases`, `alerts` e `reports`.
- `MISSING_TS_TYPE_GENERATION`: frontend sem pipeline de tipos OpenAPI.
  - Correcao: `generate:api-types` com `openapi-typescript`.

## Typecheck e smoke

Typecheck frontend:

```bash
cd services/frontend
npm ci
npm run generate:api-types
npm run typecheck
```

Smoke E2E critico:

```bash
TEST_STACK_UP=1 npx playwright test e2e/tests/ui-api-contracts.spec.ts
```

## Breaking changes e compatibilidade

- Alteracoes deste PR sao backward-compatible para as rotas existentes.
- O novo endpoint de detalhe `/report-packages/{rp_id}` e aditivo.
- Campos existentes em listagens foram preservados.

## Riscos residuais

- Cobertura E2E de autorizacao negativa/cross-tenant profunda permanece no PR-09.
- Contratos fora dos fluxos criticos (outras telas) ainda podem exigir tipagem incremental.
- Drift de contrato depende da execucao do check de CI para `generate:api-types`.

## Backlog de contrato

- Expandir testes de contrato para endpoints de investigacao detalhada (`/alerts/{id}/related-transactions`).
- Incluir smoke de `cases/new` com fixture deterministica de criacao.
- Incluir contrato de erro padronizado (4xx/5xx) com schema comum.
