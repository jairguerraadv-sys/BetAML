"""Configurações centralizadas via env vars (pydantic-settings).

⚠️  SEGURANÇA - SECRETS MANAGEMENT ⚠️
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NUNCA commit .env com secrets reais no repositório.
Em ambientes de staging/produção, NÃO use os valores padrão abaixo.

OBRIGATÓRIO em produção:
  1. JWT_SECRET: gerar com `python -c "import secrets; print(secrets.token_hex(32))"`
  2. PII_ENCRYPTION_KEY: gerar com `python -c "import secrets; print(secrets.token_urlsafe(32))"`

RECOMENDAÇÃO FORTE para produção:
  - Usar secrets manager (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault)
  - Rotação automática de secrets a cada 90 dias
  - Audit trail de acessos aos secrets

Exemplo de migração para AWS Secrets Manager:
  ```python
  import boto3
  secrets_client = boto3.client('secretsmanager', region_name='us-east-1')
  response = secrets_client.get_secret_value(SecretId='betaml/prod/jwt-secret')
  jwt_secret = json.loads(response['SecretString'])['jwt_secret']
  ```

Veja docs/security-secrets-management.md para guia completo.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    project_name: str = "BetAML"
    environment: str = "development"
    debug: bool = False

    # Database
    database_url: str = "postgresql://betaml:devpass@localhost:5432/betaml_dev"

    # Redis
    redis_url: str = "redis://:devpass@localhost:6379/0"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"

    # JWT
    # AVISO: altere JWT_SECRET para um valor aleatório em staging/prod.
    # Gere com: python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_min: int = 60

    # CORS
    # Em produção, defina CORS_ALLOW_ORIGINS como lista separada por vírgula,
    # ex.: "https://app.betaml.io,https://admin.betaml.io"
    cors_allow_origins: str = "http://localhost:3000"

    # MinIO / S3
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minio"
    minio_secret_key: str = "minio123"
    minio_bucket: str = "betaml-lakehouse"

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "betaml"

    # Encryption key for PII (AES-256 — base64 encoded 32 bytes)
    pii_encryption_key: str = "ZGV2LXNlY3JldC1lbmNyeXB0aW9uLWtleS0zMmJ5"

    # Ingest DLQ
    dlq_max_retries: int = 3

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
