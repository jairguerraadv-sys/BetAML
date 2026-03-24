# BetAML E2E (Playwright)

Este diretório contém a suíte E2E oficial do projeto.

## Pré-requisitos

- Stack local em execução (`api` + `frontend`)
- Dependências Node instaladas em `e2e/`
- Browser do Playwright instalado (`chromium`)

Comandos rápidos:

```bash
cd e2e
npm ci
npx playwright install chromium
```

## Execução

Smoke:

```bash
cd e2e
npm run test:smoke
```

Extended:

```bash
cd e2e
npm run test:extended
```

Security:

```bash
cd e2e
npm run test:security
```

Nightly:

```bash
cd e2e
npm run test:nightly
```

## Variáveis de ambiente

Credenciais e URLs básicas (normalmente em `.env.e2e`):

- `BASE_URL` (default: `http://localhost:3000`)
- `E2E_API_URL` (default: `http://localhost:8000`)
- `E2E_USERNAME`, `E2E_PASSWORD`
- `E2E_ADMIN_USERNAME`, `E2E_ADMIN_PASSWORD`
- `E2E_SECONDARY_ADMIN_USERNAME`, `E2E_SECONDARY_ADMIN_PASSWORD`
- `E2E_AUDITOR_USERNAME`, `E2E_AUDITOR_PASSWORD`

Controles do wrapper `scripts/run-playwright.sh`:

- `PW_LAUNCH_RETRIES` (default: `8`)
: número máximo de tentativas totais para falhas transitórias.
- `PW_REQUIRE_STACK_HEALTH` (default: `1`)
: habilita preflight obrigatório de frontend + API antes de cada tentativa.
- `PW_HEALTH_TIMEOUT_SEC` (default: `60`)
: timeout do preflight de saúde da stack.
- `PW_CAPTURE_DOCKER_DIAGNOSTICS` (default: `1`)
: em falhas de preflight/transientes, captura `docker compose ps` + logs.
- `PW_DIAGNOSTIC_LOG_LINES` (default: `120`)
: quantidade de linhas por serviço nos logs de diagnóstico.
- `PW_COMPOSE_FILE` (default: `../infra/docker-compose.yml`)
: caminho do compose usado para diagnóstico automático.

Controles de retry de API no helper E2E:

- `E2E_API_RETRIES` (default: `3`)
: tentativas para chamadas transientes (5xx/429/408) em fluxos críticos.

## Artefatos

- HTML: `e2e/playwright-report/<spec-slug>/`
- JUnit XML: `e2e/test-results/*.xml`

## Troubleshooting

- Se houver timeout de login/beforeEach:
  - verifique `frontend` em `http://localhost:3000`
  - verifique API em `http://localhost:8000/health/live`
- Em falhas transitórias, o wrapper já imprime snapshot de:
  - `docker compose ps`
  - logs de `api`
  - logs de `frontend`
