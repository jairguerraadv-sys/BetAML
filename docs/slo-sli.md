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
- SLI: suite smoke Playwright (auth + cases).
- SLO: 100% em main, 98% em janelas semanais.

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
