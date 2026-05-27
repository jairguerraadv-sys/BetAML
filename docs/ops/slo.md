# SLOs

| SLI | SLO inicial | Alerta |
|---|---:|---|
| Disponibilidade API | 99.5% mensal | erro 5xx/health failing |
| Latencia API p95 | < 500 ms | p95 > 1 s por 10 min |
| Ingest lag | < 5 min | lag > 15 min |
| Kafka consumer lag | < 10k msgs | crescimento continuo |
| DLQ rate | < 1% | > 5% por 10 min |
| Erro de ingestao | < 2% | > 5% por 10 min |
| Falha de login | baseline + 3 sigma | pico por tenant/IP |
| Falha report package | < 1% | qualquer sequencia > 3 |
| ML unavailable | < 15 min | indisponivel > 5 min |
| Rules unavailable | < 15 min | indisponivel > 5 min |

Runbooks relacionados ficam em `docs/ops/runbooks.md` e `docs/ops/incident-response.md`.
