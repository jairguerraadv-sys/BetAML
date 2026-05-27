from __future__ import annotations

import pytest

from services.api.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_EPSILON_WEBHOOK_SECRET,
    DEFAULT_INTERNAL_WEBHOOK_SECRET,
    DEFAULT_JWT_SECRET,
    DEFAULT_MINIO_SECRET_KEY,
    DEFAULT_PII_ENCRYPTION_KEY,
    DEFAULT_REDIS_URL,
    Settings,
)


SECURE_KWARGS = {
    "jwt_secret": "jwt-secret-for-staging-tests-32-bytes",
    "pii_encryption_key": "pii-secret-for-staging-tests-32-bytes",
    "epsilon_webhook_secret": "epsilon-secret-for-staging-tests",
    "internal_webhook_secret": "internal-secret-for-staging-tests",
    "database_url": "postgresql://betaml_app:strong@postgres:5432/betaml",
    "redis_url": "redis://:strong@redis:6379/0",
    "minio_secret_key": "strong-minio-secret",
}


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("jwt_secret", DEFAULT_JWT_SECRET, "JWT_SECRET"),
        ("pii_encryption_key", DEFAULT_PII_ENCRYPTION_KEY, "PII_ENCRYPTION_KEY"),
        ("epsilon_webhook_secret", DEFAULT_EPSILON_WEBHOOK_SECRET, "EPSILON_WEBHOOK_SECRET"),
        ("internal_webhook_secret", DEFAULT_INTERNAL_WEBHOOK_SECRET, "INTERNAL_WEBHOOK_SECRET"),
        ("database_url", DEFAULT_DATABASE_URL, "DATABASE_URL"),
        ("redis_url", DEFAULT_REDIS_URL, "REDIS_URL"),
        ("minio_secret_key", DEFAULT_MINIO_SECRET_KEY, "MINIO_SECRET_KEY"),
    ],
)
def test_staging_rejects_default_runtime_secret(field: str, value: str, match: str, monkeypatch):
    monkeypatch.setenv("EXTERNAL_VALIDATION_PROVIDER", "real_provider")
    kwargs = dict(SECURE_KWARGS)
    kwargs[field] = value

    with pytest.raises(ValueError, match=match):
        Settings(environment="staging", **kwargs)


def test_staging_rejects_mock_external_validation_provider(monkeypatch):
    monkeypatch.setenv("EXTERNAL_VALIDATION_PROVIDER", "mock_identity")

    with pytest.raises(ValueError, match="EXTERNAL_VALIDATION_PROVIDER"):
        Settings(environment="staging", **SECURE_KWARGS)


def test_onprem_staging_requires_onprem_tenant_id(monkeypatch):
    monkeypatch.setenv("EXTERNAL_VALIDATION_PROVIDER", "real_provider")

    with pytest.raises(ValueError, match="ONPREM_TENANT_ID"):
        Settings(environment="staging", deployment_mode="onprem", **SECURE_KWARGS)


def test_onprem_staging_accepts_explicit_tenant_id(monkeypatch):
    monkeypatch.setenv("EXTERNAL_VALIDATION_PROVIDER", "real_provider")

    settings = Settings(
        environment="staging",
        deployment_mode="onprem",
        onprem_tenant_id="00000000-0000-0000-0000-000000000001",
        **SECURE_KWARGS,
    )

    assert settings.deployment_mode == "onprem"
