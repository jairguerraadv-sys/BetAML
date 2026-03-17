from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("JWT_SECRET", "test-secret-only-for-unit-tests")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MIN", "15")
os.environ.setdefault("PII_ENCRYPTION_KEY", "test-pii-encryption-key-32bytes!!")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "services/api"))


def _request_with_refresh_cookie(token: str) -> StarletteRequest:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/refresh",
        "headers": [(b"cookie", f"betaml_refresh_token={token}".encode("utf-8"))],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    request = StarletteRequest(scope)
    request.state.view_rate_limit = None
    return request


class TestRefreshTokenFlow:
    def test_create_refresh_token_contains_refresh_type_and_jti(self):
        from auth import create_refresh_token
        from jose import jwt

        token, jti = create_refresh_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])

        assert payload["token_type"] == "refresh"
        assert payload["jti"] == jti

    @pytest.mark.asyncio
    async def test_get_current_user_rejects_refresh_token_as_access(self):
        from auth import create_refresh_token, get_current_user

        refresh_token, _ = create_refresh_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})

        with pytest.raises(HTTPException) as exc:
            await get_current_user(token=refresh_token, db=AsyncMock())

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_rotates_token_and_persists_new_jti(self):
        from routers.auth import refresh

        db = AsyncMock()
        user = MagicMock()
        user.id = "u1"
        user.tenant_id = "t1"
        user.role = "ADMIN"
        user.active = True

        from auth import create_refresh_token

        current_refresh_token, current_jti = create_refresh_token(
            {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
        )
        user.refresh_token_jti = current_jti

        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db.execute = AsyncMock(return_value=result)

        request = _request_with_refresh_cookie(current_refresh_token)

        with patch("routers.auth.store_refresh_token_jti", AsyncMock()) as mock_store, patch(
            "routers.auth.write_audit", AsyncMock()
        ), patch("slowapi.Limiter._check_request_limit", MagicMock()):
            resp = await refresh(request=request, response=StarletteResponse(), db=db)

        assert resp.access_token
        assert resp.refresh_token
        mock_store.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_rejects_rotated_or_revoked_jti(self):
        from routers.auth import refresh

        db = AsyncMock()
        user = MagicMock()
        user.id = "u1"
        user.tenant_id = "t1"
        user.role = "ADMIN"
        user.active = True
        user.refresh_token_jti = "different-jti"

        from auth import create_refresh_token

        refresh_token, _ = create_refresh_token({"sub": user.id, "tenant_id": user.tenant_id, "role": user.role})

        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db.execute = AsyncMock(return_value=result)

        request = _request_with_refresh_cookie(refresh_token)

        with patch("slowapi.Limiter._check_request_limit", MagicMock()):
            with pytest.raises(HTTPException) as exc:
                await refresh(request=request, response=StarletteResponse(), db=db)

        assert exc.value.status_code == 401
