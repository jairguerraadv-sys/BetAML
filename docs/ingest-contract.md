# BetAML - Contrato Oficial de Ingestao

Atualizado em: 2026-05-27
Versao do contrato: 2026-05-27.v2

## Objetivo

Definir de forma explicita o caminho oficial de ingestao para evitar drift entre documentacao, runtime e observabilidade.

## Modo oficial

- `INGEST_PIPELINE_MODE=canonical-first`

No modo `canonical-first`, a API de ingestao valida payload, aplica `MappingConfig` quando aplicavel e publica diretamente em topicos `canonical.*`.

## Caminho oficial

1. Entrada via API (`/ingest/event`, `/ingest/batch`, `/ingest/file`, `/ingest/webhook/epsilon`)
2. Validacao e normalizacao no backend
3. Publicacao em `canonical.*`
4. Processamento por `stream-processor` e `rules-engine`
5. Materializacao de alertas/casos no caminho oficial ativo

## Topicos oficiais

- `canonical.players`
- `canonical.transactions`
- `canonical.bets`
- `canonical.device_events`
- `canonical.kyc_events`
- `canonical.responsible_gambling_events`
- `canonical.account_status_changes`

## Envelope minimo de evento (obrigatorio)

Todo evento publicado no pipeline oficial deve incluir os campos abaixo:

- `event_id` (string, UUID ou identificador unico de evento)
- `tenant_id` (string)
- `correlation_id` (string para rastreio ponta-a-ponta)
- `source_event_id` (string de idempotencia de origem)
- `schema_version` (string)
- `entity_type` (string)
- `occurred_at` (timestamp ISO-8601 UTC)
- `payload` (objeto JSON)

Eventos sem esse envelope minimo podem ser rejeitados no processamento e roteados para DLQ.

## Contrato minimo de metadata em DLQ

Quando uma mensagem falha apos retries ou falha de validacao, o registro de DLQ deve preservar:

- `original_topic`
- `original_partition`
- `original_offset`
- `tenant_id`
- `source_event_id`
- `correlation_id`
- `error_type` (`validation_error`, `transient_error` ou `processing_error`)
- `error_message` (sanitizada)
- `retry_count`
- `failed_at`
- `original_message` ou `payload` (sanitizado)

Topico de DLQ em runtime:

- `BETAML_DLQ_TOPIC` quando configurado
- fallback para `<topico_origem>.dlq` quando vazio

## Semantica de retry, replay e idempotencia

- Publicacao Kafka aplica retries ate `DLQ_MAX_RETRIES`; ao exceder, publica em DLQ.
- Replay de erro de ingestao aplica dedupe por chave Redis NX:
	`betaml:replay:dedupe:<tenant_id>:<source_system>:<source_event_id>`.
- Se chave de replay ja existe, a API retorna `already_processed`.
- `stream_processor` usa commit manual de offset: somente apos sucesso de processamento
	ou apos publicacao em DLQ bem-sucedida.

## Trilha legado

Topicos `raw.*` permanecem disponiveis para compatibilidade e backfill controlado, mas nao representam o caminho oficial de operacao do pipeline.

## Monitoramento do contrato

- Endpoint operacional: `GET /ingest/contract`
- Metrica Prometheus: `betaml_ingest_contract`

Essas superficies devem ser usadas no readiness para validar que o modo de pipeline em runtime coincide com o contrato documentado.
