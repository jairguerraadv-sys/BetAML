# Pipeline DLQ Replay - PR-05

Atualizado em: 2026-05-27
Status: implementado localmente, validacao CI pendente

## Inventario do pipeline real

### Antes (diagnostico)

| Componente | Tipo | Entrada | Saida | Retry | DLQ | Observacao |
| --- | --- | --- | --- | --- | --- | --- |
| API ingest | producer | HTTP | canonical.* | sim (envio Kafka) | sim (topic.dlq) | canonical-first ja vigente |
| stream_processor | consumer/producer | raw.* / canonical.* / ingest.jobs* | canonical.*, features.player_daily, scoring.alerts | parcial | parcial | falha no loop principal nao publicava sempre em DLQ |
| ingest job reprocess | producer | ingest.jobs / ingest.jobs.reprocess | canonical.* | sim | sim | DLQ sem metadata padronizada |
| replay de erro | producer | ingest_errors | canonical.* | sim | sim | sem guarda idempotente explicita por source_event_id |

### Depois (PR-05)

| Componente | Tipo | Entrada | Saida | Retry | DLQ | Observacao |
| --- | --- | --- | --- | --- | --- | --- |
| API ingest | producer | HTTP | canonical.* | DLQ_MAX_RETRIES | BETAML_DLQ_TOPIC ou topic.dlq | payload/erro sanitizados |
| stream_processor | consumer/producer | raw.* / canonical.* / ingest.jobs* | canonical.*, features.player_daily, scoring.alerts | DLQ_MAX_RETRIES | BETAML_DLQ_TOPIC ou topic.dlq | commit manual apos sucesso ou DLQ |
| replay de erro | producer | ingest_errors | canonical.* | DLQ_MAX_RETRIES | sim | dedupe por Redis NX (tenant+source_system+source_event_id) |
| dedupe runtime | guard | mensagens consumidas | no-op seguro quando duplicada | n/a | n/a | chave dedup com TTL (default 7 dias) |

## Topicos mapeados

- Entrada legada/backfill: raw.transactions, raw.bets, raw.device_events
- Entrada oficial: canonical.transactions, canonical.bets, canonical.device_events, canonical.kyc_events, canonical.responsible_gambling_events, canonical.account_status_changes
- Jobs: ingest.jobs, ingest.jobs.reprocess
- Derivados: features.player_daily, scoring.alerts
- DLQ: BETAML_DLQ_TOPIC (quando configurado) ou fallback por topico (<topic>.dlq)

## Contrato operacional minimo de evento

Campos minimos usados no runtime:

- event_id
- tenant_id
- correlation_id
- source_event_id
- schema_version
- entity_type
- occurred_at
- payload

## Contrato operacional minimo de DLQ

Campos publicados em DLQ:

- original_topic
- original_partition
- original_offset
- source_event_id
- tenant_id
- correlation_id
- error_type (validation_error|transient_error|processing_error)
- error_message (sanitizado)
- retry_count
- failed_at
- original_message/payload (sanitizado)

## Estrategia de retry

- Publicacao principal tenta ate DLQ_MAX_RETRIES.
- Ao exceder limite, publica na DLQ.
- Erros de validacao entram como validation_error.
- Erros de conectividade/timeouts entram como transient_error.
- Nenhuma falha fica silenciosa: sempre log estruturado e metrica.

## Estrategia de idempotencia e replay

- Replay de ingest_error usa chave Redis NX:
  - betaml:replay:dedupe:<tenant_id>:<source_system>:<source_event_id>
- Se a chave ja existir, replay retorna already_processed.
- Loop do stream processor aplica claim dedupe por evento antes do processamento.

## Commit/offset safety

- Consumer do stream_processor roda com enable_auto_commit=false.
- Commit acontece apenas quando:
  - mensagem foi processada com sucesso, ou
  - mensagem foi publicada com sucesso na DLQ.
- Se DLQ falhar, offset nao e commitado.

## Como testar localmente

Unit:

- pytest tests/unit/test_stream_processor_dlq.py -q
- pytest tests/unit/test_ingest_pipeline.py -q

Integration (stack):

- TEST_STACK_UP=1 pytest tests/integration/test_dlq_replay.py -q
- TEST_STACK_UP=1 pytest tests/integration/test_stream_processor_e2e.py -q

## Risco residual

- Cobertura de integracao ainda depende de stack local Redpanda/Kafka.
- Dedupe atual usa Redis TTL (janela operacional), nao tabela historica infinita.
- Dashboards/alertas de capacidade ainda ficam para etapa de staging/capacity.
