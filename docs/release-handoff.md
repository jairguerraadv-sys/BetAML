# BetAML - Release Handoff

Atualizado em: 2026-05-11

## Estado atual

- Candidato de release validado: `74c9e14ece1ed48e83af5fda75fe1744dea26f37`
- Gate remoto encerrado: workflow `Release Readiness`, run `25696032708`, `conclusion=success`
- Evidencias locais e remotas consolidadas em `artifacts/readiness/`
- Fixes que fecharam os bloqueios remotos:
  - `2218016` — propagacao de `API_AUTO_SEED` para o container da API
  - `74c9e14` — versionamento do lockfile do E2E para `npm ci`

## O que precisa ser definido no corte

- `backup_reference` real com idade inferior a 24h
- `rollback_target` operacional real
  - Helm: revision ou tag de imagem
  - Compose: tag de imagem anterior ou hash operacional aprovado
- `oncall_owner` e janela de acompanhamento dos primeiros 60 minutos
- secrets e providers reais fora de modo local/mock

## Sequencia recomendada de deploy

1. Confirmar precondicoes operacionais.

```bash
bash scripts/check_github_actions_readiness.sh
bash scripts/check_github_workflow_sync.sh
```

2. Aplicar migracoes antes do restart da aplicacao.

```bash
bash scripts/postgres_migrate_existing.sh
cd services/api && DATABASE_URL="$PROD_DATABASE_URL" alembic upgrade head
```

3. Executar o deploy pelo alvo correspondente.

Helm:

```bash
helm upgrade betaml helm/betaml \
  --namespace betaml \
  --set api.image.tag=74c9e14 \
  --set mlService.image.tag=74c9e14 \
  --atomic \
  --timeout 10m \
  --wait

kubectl rollout status deployment/betaml-api -n betaml
```

Docker Compose:

```bash
docker compose -f infra/docker-compose.yml build api ml_service
docker compose -f infra/docker-compose.yml up -d --no-deps api ml_service frontend
```

## Smoke pos-release

Primeiros 15 minutos:

```bash
curl -sf "$API_URL/health/live"
curl -sf "$API_URL/health/ready"
curl -sf "$FRONTEND_URL/"

TOKEN=$(curl -s -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"<usuario-operacional>","password":"<senha-operacional>"}' | jq -r .access_token)

curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/stats/pld-kpis"
curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/stats/data-quality"
curl -sf -H "Authorization: Bearer $TOKEN" "$API_URL/sanctions/status"
```

Primeiros 60 minutos:

- Confirmar 5xx < 1% e p95 dentro da faixa esperada
- Confirmar criacao de alertas e casos sem backlog anormal
- Confirmar ausencia de crescimento anormal em `ingest_errors`
- Revisar Grafana, Loki e alertas ativos
- Registrar resultado final no canal operacional com backup, rollback target e horario de encerramento

## Critério de rollback

Acionar rollback se houver qualquer um dos itens abaixo apos a janela inicial de observacao:

- falha persistente em `/health/live` ou `/health/ready`
- 5xx sustentado acima do limite operacional
- regressao de login, alertas, casos ou tenant isolation
- falha de migracao nao retrocompativel ou degradacao severa de ingestao

Para rollback, seguir `docs/runbook-deploy.md` e registrar a revisao efetivamente restaurada.

## Referencias

- `docs/go-live-checklist.md`
- `docs/ops-guide.md`
- `docs/runbook-deploy.md`
- `artifacts/readiness/release-go-no-go.txt`
- `artifacts/readiness/release-readiness-remote.txt`