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

Defina SECRETS_PROVIDER para usar um backend externo:
  - "env"  → padrão (lê de variáveis de ambiente ou .env)
  - "aws"  → AWS Secrets Manager (requer SECRET_ARN ou SECRETS_PREFIX)
  - "azure" → Azure Key Vault (requer AZURE_VAULT_URL)

Veja docs/security-secrets-management.md para guia completo.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import json
import logging
import os
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

_log = logging.getLogger(__name__)

DEFAULT_JWT_SECRET = "dev-secret-change-me"
DEFAULT_EPSILON_WEBHOOK_SECRET = "dev-secret-change-me"
DEFAULT_PII_ENCRYPTION_KEY = "ZGV2LXNlY3JldC1lbmNyeXB0aW9uLWtleS0zMmJ5"
DEFAULT_INTERNAL_WEBHOOK_SECRET = "dev-webhook-secret-change-me"
DEFAULT_DATABASE_URL = "postgresql://betaml:devpass@localhost:5432/betaml_dev"
DEFAULT_REDIS_URL = "redis://:devpass@localhost:6379/0"
DEFAULT_MINIO_SECRET_KEY = "minio123"


# ── Secret provider abstraction ──────────────────────────────────────────────
def _resolve_secrets_from_provider() -> dict[str, str]:
    """Resolve secrets de um backend externo, sobrescrevendo env vars.

    Retorna um dict {ENV_VAR_NAME: value} que será injetado em ``os.environ``
    antes de ``Settings()`` ser instanciado pelo pydantic-settings.

    Providers suportados:
      - ``env`` (default): não faz nada — secrets já estão em env vars.
      - ``aws``: lê de AWS Secrets Manager (``SECRET_ARN`` ou ``SECRETS_PREFIX``).
      - ``azure``: lê de Azure Key Vault (``AZURE_VAULT_URL``).
    """
    provider = os.getenv("SECRETS_PROVIDER", "env").lower()
    if provider == "env":
        return {}

    if provider == "aws":
        return _resolve_aws_secrets()

    if provider == "azure":
        return _resolve_azure_secrets()

    _log.warning("unknown_secrets_provider provider=%s — falling back to env", provider)
    return {}


def _resolve_aws_secrets() -> dict[str, str]:
    """Lê secrets de AWS Secrets Manager e retorna dict de env vars."""
    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "SECRETS_PROVIDER=aws requires boto3. Install with: pip install boto3"
        ) from None

    secret_arn = os.getenv("SECRET_ARN", "")
    prefix = os.getenv("SECRETS_PREFIX", "betaml/prod")
    region = os.getenv("AWS_REGION", "us-east-1")

    client = boto3.client("secretsmanager", region_name=region)

    if secret_arn:
        resp = client.get_secret_value(SecretId=secret_arn)
        secrets = json.loads(resp["SecretString"])
        _log.info("aws_secrets_loaded arn=%s keys=%d", secret_arn, len(secrets))
        return {k.upper(): v for k, v in secrets.items()}

    # Fallback: lê secrets individuais por convenção de nome
    keys = [
        ("JWT_SECRET", f"{prefix}/jwt-secret"),
        ("PII_ENCRYPTION_KEY", f"{prefix}/pii-encryption-key"),
        ("MINIO_SECRET_KEY", f"{prefix}/minio-secret-key"),
        ("INTERNAL_WEBHOOK_SECRET", f"{prefix}/internal-webhook-secret"),
        ("DATABASE_URL", f"{prefix}/database-url"),
    ]
    resolved: dict[str, str] = {}
    for env_name, secret_id in keys:
        try:
            resp = client.get_secret_value(SecretId=secret_id)
            resolved[env_name] = resp["SecretString"]
        except client.exceptions.ResourceNotFoundException:
            _log.debug("aws_secret_not_found id=%s", secret_id)
    _log.info("aws_secrets_loaded prefix=%s keys=%d", prefix, len(resolved))
    return resolved


def _resolve_azure_secrets() -> dict[str, str]:
    """Lê secrets de Azure Key Vault e retorna dict de env vars."""
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore[import-untyped]
        from azure.keyvault.secrets import SecretClient  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "SECRETS_PROVIDER=azure requires azure-identity and azure-keyvault-secrets. "
            "Install with: pip install azure-identity azure-keyvault-secrets"
        ) from None

    vault_url = os.getenv("AZURE_VAULT_URL", "")
    if not vault_url:
        raise RuntimeError("SECRETS_PROVIDER=azure requires AZURE_VAULT_URL to be set")

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    mapping = {
        "JWT_SECRET": "betaml-jwt-secret",
        "PII_ENCRYPTION_KEY": "betaml-pii-encryption-key",
        "MINIO_SECRET_KEY": "betaml-minio-secret-key",
        "INTERNAL_WEBHOOK_SECRET": "betaml-internal-webhook-secret",
        "DATABASE_URL": "betaml-database-url",
    }
    resolved: dict[str, str] = {}
    for env_name, secret_name in mapping.items():
        try:
            secret = client.get_secret(secret_name)
            resolved[env_name] = secret.value
        except Exception:
            _log.debug("azure_secret_not_found name=%s", secret_name)
    _log.info("azure_secrets_loaded vault=%s keys=%d", vault_url, len(resolved))
    return resolved


# ── Inject resolved secrets into env BEFORE Settings() ──────────────────────
_provider_secrets = _resolve_secrets_from_provider()
for _k, _v in _provider_secrets.items():
    os.environ.setdefault(_k, _v)


class Settings(BaseSettings):
    # App
    project_name: str = "BetAML"
    environment: str = "development"
    debug: bool = False

    # Deployment mode
    # saas    → multi-tenant SaaS hospedado pela BetAML (default)
    # onprem  → single-tenant instalado na infra do operador;
    #            exige ONPREM_TENANT_ID setado em não-dev.
    deployment_mode: Literal["saas", "onprem"] = "saas"
    # UUID do tenant pré-seed em deployments on-prem.
    # Obrigatório quando deployment_mode=onprem e environment != development/test.
    onprem_tenant_id: str | None = None

    # Database
    database_url: str = DEFAULT_DATABASE_URL

    # Redis
    redis_url: str = DEFAULT_REDIS_URL

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    redpanda_admin_url: str = "http://redpanda:9644"

    # JWT
    # AVISO: altere JWT_SECRET para um valor aleatório em staging/prod.
    # Gere com: python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_min: int = 60
    epsilon_webhook_secret: str = DEFAULT_EPSILON_WEBHOOK_SECRET

    # CORS
    # Em produção, defina CORS_ALLOW_ORIGINS como lista separada por vírgula,
    # ex.: "https://app.betaml.io,https://admin.betaml.io"
    cors_allow_origins: str = "http://localhost:3000"

    # MinIO / S3
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minio"
    minio_secret_key: str = DEFAULT_MINIO_SECRET_KEY
    minio_bucket: str = "betaml-lakehouse"

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "betaml"

    # Internal service endpoints / metrics
    ml_service_url: str = "http://ml-service:8001"
    rules_engine_metrics_url: str = "http://rules-engine:8002/metrics"
    stream_processor_metrics_url: str = "http://stream-processor:8003/metrics"

    # Encryption key for PII (AES-256 — base64 encoded 32 bytes)
    pii_encryption_key: str = DEFAULT_PII_ENCRYPTION_KEY

    # Ingest DLQ
    dlq_max_retries: int = 3

    # Internal webhook secret (AlertManager → /internal/alerts/webhook)
    # Em produção, gere com: python -c "import secrets; print(secrets.token_hex(32))"
    internal_webhook_secret: str = DEFAULT_INTERNAL_WEBHOOK_SECRET

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug_flag(cls, value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on", "debug", "development"}:
            return True
        if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
            return False
        return value

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value):
        if value is None:
            return "development"
        return str(value).strip().lower()

    @field_validator(
        "pii_encryption_key",
        "jwt_secret",
        "epsilon_webhook_secret",
        "internal_webhook_secret",
        mode="before",
    )
    @classmethod
    def reject_blank_security_secrets(cls, value):
        if value is None or not str(value).strip():
            raise ValueError("Security secret cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Rejeita secrets padrão em ambientes não-dev na instanciação."""
        if self.environment not in ("development", "test"):
            if self.jwt_secret == DEFAULT_JWT_SECRET:
                raise ValueError(
                    "JWT_SECRET must be changed from default in staging/production. "
                    "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.epsilon_webhook_secret == DEFAULT_EPSILON_WEBHOOK_SECRET:
                raise ValueError(
                    "EPSILON_WEBHOOK_SECRET must be changed from default in staging/production. "
                    "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.pii_encryption_key == DEFAULT_PII_ENCRYPTION_KEY:
                raise ValueError(
                    "PII_ENCRYPTION_KEY must be changed from default in staging/production. "
                    "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\". "
                    "WARNING: changing this key invalidates all encrypted CPFs in the database!"
                )
            if self.internal_webhook_secret == DEFAULT_INTERNAL_WEBHOOK_SECRET:
                raise ValueError(
                    "INTERNAL_WEBHOOK_SECRET must be changed from default in staging/production. "
                    "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.database_url == DEFAULT_DATABASE_URL:
                raise ValueError(
                    "DATABASE_URL must be changed from the local dev default in staging/production."
                )
            if self.redis_url == DEFAULT_REDIS_URL:
                raise ValueError(
                    "REDIS_URL must be changed from the local dev default in staging/production."
                )
            if self.minio_secret_key == DEFAULT_MINIO_SECRET_KEY:
                raise ValueError(
                    "MINIO_SECRET_KEY must be changed from default in staging/production."
                )
            # On-prem: exige tenant pré-seed configurado
            if self.deployment_mode == "onprem" and not self.onprem_tenant_id:
                raise ValueError(
                    "ONPREM_TENANT_ID must be set when DEPLOYMENT_MODE=onprem in staging/production. "
                    "Set it to the UUID of the pre-seeded tenant."
                )
        return self

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
