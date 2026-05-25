# BetAML OpenAPI por Tag

O snapshot oficial do contrato esta em [`docs/openapi.json`](/workspaces/BetAML/docs/openapi.json). A API live expõe o mesmo documento em `GET /openapi.json`.

## Tags principais

| Tag | Escopo |
|---|---|
| `auth` | login, refresh, logout, sessao atual |
| `ingest` | ingestao por evento, batch, arquivo, webhook, jobs, streaming e quarentena |
| `rules` | CRUD de regras DSL, simulacao, macros e compound rules |
| `features` | feature store online/offline, historico e qualidade |
| `alerts` | listagem, triagem, labeling e explicabilidade |
| `cases` | workflow de casos, timeline, uploads e report packages |
| `reports` | relatorio regulatorio mensal e export |
| `admin` | tenant settings, usuarios, onboarding, API keys e maintenance mode |
| `audit` | trilha de auditoria e filtros de compliance |

## Exemplos por tag

### `auth`

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin_a","password":"admin123","tenant_slug":"operador_a"}'
```

### `ingest`

```bash
curl -X POST http://localhost:8000/ingest/jobs/<job-id>/reprocess \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"reprocess after mapping fix","mapping_version_id":"<version-id>"}'
```

### `rules`

```bash
curl -X POST http://localhost:8000/rules/<rule-id>/simulate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date_from":"2026-03-01T00:00:00Z","date_to":"2026-03-20T23:59:59Z","player_ids":["player-1"]}'
```

### `features`

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/feature-store/players/<player-id>/history?from=2026-03-01T00:00:00Z&to=2026-03-20T23:59:59Z"
```

### `alerts`

```bash
curl -X POST http://localhost:8000/alerts/<alert-id>/label \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"TRUE_POSITIVE","note":"Padrao confirmado apos investigacao"}'
```

### `cases`

```bash
curl -X POST http://localhost:8000/cases/<case-id>/report-package \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"FILE_SAR","analyst_narrative":"Narrativa consolidada do caso"}'
```

### `reports`

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/reports/monthly-summary?date_from=2026-03-01&date_to=2026-03-31"
```

### `admin`

```bash
curl -X PUT "http://localhost:8000/admin/maintenance-mode?enabled=true" \
  -H "Authorization: Bearer $TOKEN"
```

### `audit`

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/audit-logs?action=promote_model&pii_only=false"
```

## Contratos de Listagem (modo legado + envelope)

Os endpoints abaixo mantêm compatibilidade retroativa com retorno em lista direta.
Quando enviado `envelope=true`, retornam o shape paginado:

```json
{
  "items": [],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

Endpoints suportados:

- `GET /rules?envelope=true&limit=50&offset=0`
- `GET /player-lists?envelope=true&limit=50&offset=0`
- `GET /notifications?envelope=true&limit=50&offset=0`
- `GET /audit-logs?envelope=true&limit=50&offset=0`

## Regeneracao

```bash
python scripts/export_openapi.py
```
