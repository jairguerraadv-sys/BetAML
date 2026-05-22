from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-only-for-unit-tests")
os.environ.setdefault("PII_ENCRYPTION_KEY", "test-pii-encryption-key-32bytes!!")

sys.path.insert(0, str(REPO_ROOT / "libs"))
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from utils import sanitize_sensitive_payload


def test_sanitize_sensitive_payload_redacts_known_keys():
    raw = {
        "name": "Joao Silva",
        "cpf": "12345678909",
        "nested": {
            "email": "joao@example.com",
            "payload": {"token": "secret"},
            "amount": 150.0,
        },
    }

    sanitized = sanitize_sensitive_payload(raw)

    assert sanitized["name"] == "[REDACTED]"
    assert sanitized["cpf"] == "[REDACTED]"
    assert sanitized["nested"]["email"] == "[REDACTED]"
    assert sanitized["nested"]["payload"]["token"] == "[REDACTED]"
    assert sanitized["nested"]["amount"] == 150.0


def test_sanitize_sensitive_payload_truncates_long_strings():
    long_text = "x" * 400
    out = sanitize_sensitive_payload({"reason": long_text})
    assert isinstance(out["reason"], str)
    assert out["reason"].endswith("...[TRUNCATED]")
