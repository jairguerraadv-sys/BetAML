# Disaster Recovery

## Objetivos iniciais

- RPO Postgres: definido por frequencia de backup/WAL.
- RTO API: restaurar servico essencial antes de analytics completos.
- MinIO/S3: preservar evidencias e report packages antes de dados derivados.

## Prioridade de restore

1. Postgres.
2. MinIO/S3.
3. Redpanda/Kafka offsets/topicos.
4. Redis.
5. ClickHouse.
6. ML artifacts.

## Exercicios

Executar restore drill periodico e registrar:

- commit/deploy testado;
- timestamps de backup;
- duracao do restore;
- checks de RLS, login, ingest, report package e audit logs.
