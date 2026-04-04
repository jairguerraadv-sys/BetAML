# BetAML - SLO/SLI Operacional

## Janela e principios

- Janela de medicao: mensal.
- Error budget aplicado por servico critico.
- Priorizacao de incidentes baseada em impacto AML e indisponibilidade.

## SLI e SLO recomendados

1. Disponibilidade API
- SLI: sucesso de `GET /health` e requests 2xx/3xx.
- SLO: >= 99.5% mensal.

2. Latencia API
- SLI: p95 de requests autenticadas.
- SLO: p95 <= 500 ms.

3. Pipeline de ingestao
- SLI: tempo `raw.* -> features.*` por evento.
- SLO: p95 <= 120 s.

4. Qualidade de processamento
- SLI: taxa de erro em `ingest_errors` por 1k eventos.
- SLO: <= 2/1000 eventos.

5. E2E de negocio
- SLI: suite smoke Playwright (`auth`, `dashboard`, `alerts`, `cases`, `global-search`, `mappings`, `player-lists`, `ingest-jobs`, `ingest-errors`, `feature-store`, `model-registry`, `audit-logs`, `reports`, `notifications`, `admin/settings`, `admin/ops`, `api-keys`).
- SLO: 100% em main, 98% em janelas semanais.
- SLI adicional: suite extended Playwright (`mappings-versioning`, `ingest-operations`, `report-exports`, `maintenance-mode`, `report-audit`, `onboarding`) em agenda semanal/readiness.
- SLO: 95% em janelas semanais.
- SLI adicional: suite security Playwright (`security-rbac`) em readiness.
- SLO: 100% dos cenários críticos por role.

## Alertas recomendados

- API 5xx > 1% por 5 min.
- p95 da API > 1s por 10 min.
- Falha de migration em deploy.
- Aumento de `ingest_errors` acima do SLO por 15 min.
- Sem novos eventos canonicos por 10 min durante horario operacional.

## Governanca de erro

- Excedeu error budget: congelar features nao criticas.
- Abrir incidente com RCA em ate 48h.
- Vincular acao corretiva com prazo e dono.

## Evidencia operacional minima

- Workflow [ .github/workflows/capacity-smoke.yml ] deve publicar artifact-capacity-smoke com CSV bruto do Locust, sumario e avaliacao objetiva de thresholds.
- Validacao automatica usa [tests/load/validate_slo.py](tests/load/validate_slo.py) sobre o request POST /ingest/batch.
- Execucao semanal agendada e uma smoke de capacidade; para aceite de go-live, disparar workflow manual com parametros reforcados e anexar a mesma evidencia ao ticket operacional.
