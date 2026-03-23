# Frontend

## Objetivo

Aplicacao Next.js 14 do BetAML para operacao AML, investigacao de casos, administracao do tenant, onboarding, observabilidade e governanca.

## Requisitos

- Node.js 20+
- npm 10+

## Variaveis de ambiente

| Variavel | Default | Descricao |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | URL publica da API usada pelo browser |
| `BACKEND_API_URL` | `http://api:8000` | URL interna usada pelas routes server-side |
| `NODE_ENV` | `development` | Ambiente Next.js |

## Rodar localmente

```bash
cd services/frontend
npm install
npm run dev
```

Build de producao:

```bash
npm run build
npm run start
```

## Paginas principais

| Rota | Objetivo |
|---|---|
| `/dashboard` | KPIs, series temporais, heatmap e top players |
| `/alerts` | fila operacional de alertas |
| `/cases` | listagem e investigacao de casos |
| `/players` | perfil e historico de jogadores |
| `/feature-store/[playerId]` | snapshot online, historico e qualidade das features |
| `/mappings` | studio de `MappingConfig` com preview e versionamento |
| `/ingest-jobs` | monitoramento e reprocessamento de jobs |
| `/ingest-errors` | quarentena e replay manual |
| `/rules` | CRUD, validacao e simulacao de regras |
| `/rules/compound` | regras compostas e macros |
| `/player-lists` | CRUD de listas para whitelist, blacklist e watchlist |
| `/model-registry` | metricas, A/B e promocao champion/challenger |
| `/reports` | relatorio regulatorio mensal e export |
| `/audit-logs` | trilha de auditoria filtravel |
| `/notifications` | notificacoes in-app |
| `/admin` | administracao do tenant e API keys |
| `/admin/onboarding` | wizard de onboarding em 5 passos |
| `/admin/ops` | observabilidade operacional |
| `/settings` | scoring, SLA, retention e rate limits |

## Recursos de UX

- atualizacao near-real-time por polling e SSE
- dark/light mode persistido em `localStorage`
- i18n em `pt-BR` e `en-US`
- mascaramento de PII por role
- componentes com `aria-label` e navegacao por teclado nos fluxos principais

## Validacao

```bash
services/frontend/node_modules/.bin/tsc -p services/frontend/tsconfig.json --noEmit
```

## Dependencias relevantes

- `next`
- `react`
- `@tanstack/react-query`
- `react-hook-form`
- `recharts`
