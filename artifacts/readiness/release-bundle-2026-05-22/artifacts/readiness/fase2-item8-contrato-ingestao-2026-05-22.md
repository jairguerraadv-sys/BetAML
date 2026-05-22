# Fase 2 - Item 8 (Contrato Oficial de Ingestao)

Data: 2026-05-22
Status: Implementado

## Objetivo

Formalizar contrato de ingestao oficial com superficie documentada e monitorada.

## Correcoes implementadas

1. Configuracao explicita de modo de pipeline
- Arquivo: services/api/config.py
- Campo: ingest_pipeline_mode
- Valores aceitos: canonical-first, raw-first

2. Endpoint operacional do contrato
- Arquivo: services/api/routers/ingest.py
- Endpoint: GET /ingest/contract
- Saida: modo, caminho oficial, topicos oficiais, versao do contrato e canais de monitoramento

3. Monitoramento Prometheus
- Arquivo: services/api/routers/ingest.py
- Metrica: betaml_ingest_contract_info

4. Documentacao operacional
- Arquivo: docs/ingest-contract.md
- Referencia adicionada em: docs/ops-guide.md

5. Cobertura de teste
- Arquivo: tests/unit/test_ingest_core.py
- Teste: test_ingest_router_has_contract_endpoint

## Validacao em runtime

1. Endpoint autenticado
- Comando: curl GET /ingest/contract com token admin_a
- Resultado: payload retornado com pipeline_mode=canonical-first e contract_version=2026-05-22.v1

2. Metrica de monitoramento
- Comando: curl GET /metrics e grep betaml_ingest_contract
- Resultado: betaml_ingest_contract_info exposta com labels do contrato

3. Regressao unit
- Comando: pytest -q tests/unit/test_ingest_core.py
- Resultado: 23 passed

## Criterio de aceite do item 8

- Arquitetura documentada: atendido
- Arquitetura monitorada: atendido
