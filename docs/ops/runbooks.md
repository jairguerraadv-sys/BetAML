# Runbooks

## API indisponivel

- Verificar health live/ready.
- Conferir Postgres, Redis e Redpanda.
- Revisar deploy recente e logs da API.
- Se afetar login, validar Redis e `JWT_SECRET`.

## Report package falhando

- Conferir permissao do usuario e status do caso.
- Validar MinIO/S3 para PDF/XML.
- Executar endpoint de chain-of-custody.
- Se hash divergir, tratar como incidente de integridade.

## Replay de DLQ

- Confirmar tenant, job e mapping version.
- Validar payload corrigido.
- Executar replay em lote pequeno.
- Conferir lineage e audit log.

## Rotacao de chaves

- Seguir `docs/ops/key-rotation.md`.
- Registrar janela, responsavel, rollback e evidencias.
