# BetAML - Guia de Contribuicao

## Objetivo

Este guia padroniza os comandos de teste e validacao para que execucao local e CI usem o mesmo fluxo.

## Pre-requisitos

- Python 3.12
- Node.js 20
- Dependencias instaladas:
  - `pip install -r services/api/requirements.txt -r requirements-dev.txt`
  - `cd services/frontend && npm ci`
  - `cd e2e && npm ci`

## Backend (gate padrao)

Comando equivalente ao CI principal:

```bash
DEBUG=false bash scripts/run_critical_unit_batches.sh --include-remainder -q --tb=short \
  --cov=services/api \
  --cov-report=term-missing \
  --cov-report=xml:coverage.xml \
  --cov-fail-under=40
```

Execucao rapida (somente modulos criticos):

```bash
DEBUG=false bash scripts/run_critical_unit_batches.sh -q --tb=short \
  --cov=services/api \
  --cov-fail-under=40
```

## Frontend

Type check estrito:

```bash
services/frontend/node_modules/.bin/tsc -p services/frontend/tsconfig.json --noEmit
```

## E2E (Playwright)

Referencia completa de variaveis e troubleshooting: `e2e/README.md`.

Rodar smoke:

```bash
cd e2e
npm run test:smoke -- --reporter=html,junit
```

Rodar extended:

```bash
cd e2e
npm run test:extended -- --reporter=html,junit
```

Rodar security:

```bash
cd e2e
npm run test:security -- --reporter=html,junit
```

Rodar nightly (agrega smoke + extended + security):

```bash
cd e2e
npm run test:nightly -- --reporter=html,junit
```

Saidas locais esperadas:

- HTML em `e2e/playwright-report/<spec-slug>/`
- JUnit em `e2e/test-results/*.xml`

Limpeza rapida de artefatos locais:

```bash
rm -rf e2e/playwright-report e2e/test-results e2e/playwright-report-* e2e/test-results-*
```

## Validacao minima antes de PR

- Backend gate batelado passando.
- TypeScript do frontend passando.
- E2E smoke passando (quando mudanca impacta UI/fluxo).
- Nenhum segredo sensivel em arquivos versionados.

## Workflows relacionados

- CI geral: `.github/workflows/ci.yml`
- E2E: `.github/workflows/e2e.yml`
- Readiness: `.github/workflows/release-readiness.yml`
- Integracao externa: `.github/workflows/external-validation-integration.yml`

## Artifacts padronizados

- `artifact-backend-coverage-ci`
- `artifact-backend-coverage-readiness`
- `artifact-backend-coverage-external-validation`
- `artifact-e2e-playwright-report`
- `artifact-e2e-playwright-results`
- `artifact-e2e-docker-logs`
- `artifact-e2e-wrapper-run-log`
- `artifact-readiness-playwright-report`
- `artifact-readiness-playwright-results`
- `artifact-readiness-docker-logs`
- `artifact-external-validation-docker-logs`
