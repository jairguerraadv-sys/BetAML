# Onda C - Contratos e Hardening (2026-05-23)

Objetivo: validar estabilidade de contrato de listagens com envelope opcional e hardening de seguranca no WS de ingestao.

## Suite executada

1. Unitario WS ingest (cenarios negativos + mapping)
- Comando:
  - pytest tests/unit/test_ingest_core.py::test_ingest_websocket_rejects_missing_bearer_header tests/unit/test_ingest_core.py::test_ingest_websocket_rejects_invalid_token tests/unit/test_ingest_core.py::test_ingest_websocket_rejects_inactive_user tests/unit/test_ingest_core.py::test_ingest_websocket_rejects_insufficient_role tests/unit/test_ingest_core.py::test_ingest_websocket_rejects_tenant_mismatch tests/unit/test_ingest_core.py::test_ingest_websocket_applies_explicit_mapping_before_publish -q --tb=short
- Resultado:
  - 6 passed

2. Unitario Audit e Notifications (contrato envelope + legado)
- Comando:
  - pytest tests/unit/test_audit.py tests/unit/test_notifications.py -q --tb=short
- Resultado:
  - 23 passed

3. Unitario Player Lists e Rules (contrato envelope + legado)
- Comando:
  - pytest tests/unit/test_player_lists_routes.py tests/unit/test_rules.py -q --tb=short
- Resultado:
  - 33 passed

4. Integracao - contratos envelope
- Comando:
  - TEST_STACK_UP=1 pytest tests/integration/test_new_endpoints.py::TestEnvelopeContracts tests/integration/test_new_endpoints.py::TestExternalValidationEndpoints::test_external_validation_tenant_isolation_by_id -q --tb=short
- Resultado:
  - 5 passed

5. Integracao - sweep de novos endpoints
- Comando:
  - TEST_STACK_UP=1 pytest tests/integration/test_new_endpoints.py -q --tb=short
- Resultado:
  - 28 passed

6. Integracao - sweep pipeline
- Comando:
  - TEST_STACK_UP=1 pytest tests/integration/test_pipeline.py -q --tb=short
- Resultado:
  - 79 passed

## Observacoes

- OpenAPI foi regenerado com scripts/export_openapi.py.
- Docs operacionais atualizados com contrato envelope opcional:
  - docs/openapi-tags.md
  - docs/ops-guide.md

## Gate final de release (rodada 2026-05-23)

- Preflight operacional (macOS compat):
  - artifacts/readiness/preflight-2026-05-23.txt
  - resultado: readiness_preflight=PASS
- Restore drill com backup local + validacao de objeto MinIO:
  - artifacts/readiness/restore-drill-2026-05-23.txt
  - resultado: restore_drill=PASS
- Decisao consolidada go/no-go:
  - artifacts/readiness/release-go-no-go-2026-05-23.txt
  - resultado: release_go_no_go=GO

- Validacao adicional do restore (download MinIO nativo):
  - artifacts/readiness/restore-drill-2026-05-23-minio-download.txt
  - resultado: restore_drill=PASS

## Ajustes de compatibilidade operacional aplicados

- scripts/readiness_preflight.sh:
  - fallback para checksum (`sha256sum` ou `shasum`)
  - leitura de serviços sem `mapfile` (compatível com Bash 3.2)
- scripts/postgres_migrate_existing.sh:
  - leitura de migrations sem `mapfile`
  - checksum com fallback `sha256sum`/`shasum`
- scripts/restore_drill.sh:
  - download de backup MinIO via `mc cat` (sem bind mount de diretório temporário)
