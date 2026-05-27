from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libs"))
sys.path.insert(0, os.path.join(ROOT, "services", "api"))


def _db_for_user(user):
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _user(user_id: str = "u1", tenant_id: str = "t1", active: bool = True):
    user = MagicMock()
    user.id = user_id
    user.tenant_id = tenant_id
    user.active = active
    user.role = "ADMIN"
    user.roles = None
    return user


@pytest.mark.asyncio
async def test_get_current_user_rejects_token_without_sub():
    from auth import create_access_token, get_current_user

    token = create_access_token({"tenant_id": "t1", "role": "ADMIN"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=_db_for_user(_user()))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_token_without_tenant_id():
    from auth import create_access_token, get_current_user

    token = create_access_token({"sub": "u1", "role": "ADMIN"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=_db_for_user(_user()))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_revoked_access_token():
    from auth import create_access_token, get_current_user
    from config import settings
    from jose import jwt

    token = create_access_token({"sub": "u1", "tenant_id": "t1", "role": "ADMIN"})
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=True)

    with patch("auth._get_auth_redis", AsyncMock(return_value=redis)):
        with pytest.raises(HTTPException) as exc:
            await get_current_user(token=token, db=_db_for_user(_user()))

    assert payload["jti"]
    assert exc.value.status_code == 401
    assert "revogado" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_validate_api_key_rejects_expired_key():
    from auth import validate_api_key

    api_key = MagicMock()
    api_key.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    api_key.active = True

    result = MagicMock()
    result.scalar_one_or_none.return_value = api_key
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    tenant_id = "00000000-0000-0000-0000-000000000001"
    raw = f"btml_{tenant_id.replace('-', '')}_secret"

    with pytest.raises(HTTPException) as exc:
        await validate_api_key(x_api_key=raw, db=db)

    assert exc.value.status_code == 401
    assert "expirada" in exc.value.detail


@pytest.mark.asyncio
async def test_validate_api_key_v2_sets_rls_context_before_lookup():
    from auth import validate_api_key

    tenant_id = "00000000-0000-0000-0000-000000000001"
    api_key = MagicMock()
    api_key.tenant_id = tenant_id
    api_key.expires_at = None
    api_key.key_prefix = "btml_00000000"

    result = MagicMock()
    result.scalar_one_or_none.return_value = api_key
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    raw = f"btml_{tenant_id.replace('-', '')}_secret"
    validated = await validate_api_key(x_api_key=raw, db=db)

    assert validated is api_key
    first_stmt = str(db.execute.await_args_list[0].args[0])
    assert "set_config('app.current_tenant'" in first_stmt


@pytest.mark.asyncio
async def test_ingest_principal_rejects_inactive_tenant_for_api_key():
    from auth import get_ingest_principal

    api_key = MagicMock()
    api_key.tenant_id = "t1"
    api_key.permissions = ["ingest"]
    tenant = MagicMock()
    tenant.active = False
    tenant.settings = {}
    db = AsyncMock()
    db.get = AsyncMock(return_value=tenant)

    with patch("auth.validate_api_key", AsyncMock(return_value=api_key)):
        with pytest.raises(HTTPException) as exc:
            await get_ingest_principal(x_api_key="btml_key", authorization=None, db=db)

    assert exc.value.status_code == 503
