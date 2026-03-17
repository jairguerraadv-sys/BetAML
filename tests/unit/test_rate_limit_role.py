from __future__ import annotations

import os
import sys

from pathlib import Path

from starlette.requests import Request as StarletteRequest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("JWT_SECRET", "test-secret-only-for-unit-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ENVIRONMENT", "test")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))


def _request(auth: str | None = None) -> StarletteRequest:
    headers = []
    if auth:
        headers.append((b"authorization", auth.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/alerts",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    req = StarletteRequest(scope)
    req.state.user_role = None
    return req


def test_rate_limit_by_state_role_admin():
    from rate_limit import get_rate_limit_by_role

    req = _request()
    req.state.user_role = "ADMIN"
    assert get_rate_limit_by_role(req) == "100/minute"


def test_rate_limit_by_state_role_analyst():
    from rate_limit import get_rate_limit_by_role

    req = _request()
    req.state.user_role = "AML_ANALYST"
    assert get_rate_limit_by_role(req) == "50/minute"


def test_rate_limit_anonymous_default():
    from rate_limit import get_rate_limit_by_role

    req = _request()
    assert get_rate_limit_by_role(req) == "10/minute"


def test_rate_limit_unknown_state_role_falls_back_anonymous():
    from rate_limit import get_rate_limit_by_role

    req = _request()
    req.state.user_role = "UNKNOWN_ROLE"
    assert get_rate_limit_by_role(req) == "10/minute"


def test_rate_limit_by_jwt_role_when_state_missing():
    from auth import create_access_token
    from rate_limit import get_rate_limit_by_role

    token = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "AUDITOR"})
    req = _request(auth=f"Bearer {token}")
    req.state.user_role = None
    assert get_rate_limit_by_role(req) == "20/minute"


def test_rate_limit_key_includes_role_tenant_ip():
    from auth import create_access_token
    from rate_limit import _get_rate_limit_key

    token = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "AUDITOR"})
    req = _request(auth=f"Bearer {token}")
    key = _get_rate_limit_key(req)

    assert key.startswith("role:AUDITOR:tenant:t1:ip:")
