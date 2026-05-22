# Fase 1 - Evidencia de Regressao API

Data: 2026-05-22
Escopo: security + auth + ingest

## Comando executado

```bash
TEST_STACK_UP=1 pytest -q \
  tests/security/test_tenant_isolation.py \
  tests/unit/test_api_auth.py \
  tests/unit/test_auth_refresh.py \
  tests/unit/test_module10_security.py \
  tests/unit/test_module8.py \
  tests/unit/test_ingest_core.py \
  tests/unit/test_ingest_extended.py \
  tests/unit/test_ingest_resilience.py \
  tests/unit/test_stream_processor_ingest_jobs.py \
  tests/unit/test_utils_payload_sanitization.py \
  --junitxml=artifacts/readiness/junit/fase1-api-regressao-2026-05-22.xml
```

## Resultado

- Total coletado: 145
- Passed: 137
- Skipped: 8
- Failed: 0
- Duracao: 28.83s

## Evidencia gerada

- JUnit XML: artifacts/readiness/junit/fase1-api-regressao-2026-05-22.xml
- Sumario desta execucao: artifacts/readiness/fase1-api-regressao-2026-05-22.md

## Observacoes

- Suite executada com stack local ativa (TEST_STACK_UP=1).
- Cobertura do pacote focada em isolamento multi-tenant, autenticacao/refresh e fluxos de ingestao/resiliencia.
