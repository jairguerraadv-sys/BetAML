# Backup e Restore

## Postgres

Backup:

```bash
scripts/backup_postgres.sh
```

Restore deve ser testado em ambiente isolado antes de qualquer producao:

```bash
scripts/restore_drill.sh
```

Validar RLS, contagem de tenants, audit logs e report packages depois do restore.

## MinIO/S3

Copiar buckets de bronze/gold/evidencias com versionamento habilitado. Evidencias e report packages devem preservar metadata de content type, tamanho e hash.

## ClickHouse

Para producao, usar backup nativo do ClickHouse ou snapshots de volume coordenados. A perda de ClickHouse afeta historico analitico/features offline, mas nao deve apagar cadeia regulatoria persistida no Postgres/MinIO.

## Redis

Redis e cache/blacklist. Perda causa reprocessamento de cache e remove blacklist de JWTs revogados. Em incidente de seguranca, rotacionar `JWT_SECRET`.

## Redpanda/Kafka

Configurar retencao por topico, DLQ e capacidade de replay. Antes de replay, registrar janela, mapping version e responsavel.
