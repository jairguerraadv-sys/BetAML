# BetAML - Contrato Oficial de Ingestao

Atualizado em: 2026-05-22
Versao do contrato: 2026-05-22.v1

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

## Trilha legado

Topicos `raw.*` permanecem disponiveis para compatibilidade e backfill controlado, mas nao representam o caminho oficial de operacao do pipeline.

## Monitoramento do contrato

- Endpoint operacional: `GET /ingest/contract`
- Metrica Prometheus: `betaml_ingest_contract`

Essas superficies devem ser usadas no readiness para validar que o modo de pipeline em runtime coincide com o contrato documentado.
