# Estrategia de Testes

## Gates

- `pytest tests/security -q`: auth, RLS, RBAC, secrets e isolamento tenant.
- `pytest tests/compliance -q`: report package e cadeia de custodia.
- `pytest tests/ingest -q`: DLQ, replay, mappings e webhook security.
- `scripts/run_critical_unit_batches.sh --critical-coverage --include-remainder`: gate batelado do backend.
- TypeScript: `services/frontend/node_modules/.bin/tsc -p services/frontend/tsconfig.json --noEmit`.

## Cobertura

Metas de transicao:

- Auth/RBAC/RLS/API key/config: 85%.
- Rules engine: 80%.
- Report package/cadeia de custodia: 80%.
- Global: pode ficar menor temporariamente, desde que o gate critico suba por fases.

O runner aceita `--critical-coverage` para aplicar o threshold critico. A expansao do threshold deve acompanhar estabilizacao dos testes legados.
