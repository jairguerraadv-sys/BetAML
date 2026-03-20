"""
tests/unit/test_module8.py — Module 8 admin + onboarding tests.

Covers:
  - usage_stats returns all expected keys
  - usage_stats counts events this month
  - usage_stats counts alerts this month
  - usage_stats counts open cases
  - get_ingest_principal with valid API key
  - get_ingest_principal with valid JWT
  - get_ingest_principal missing auth raises 401
  - get_ingest_principal API key without ingest permission raises 403
  - ScoringConfigOut has auto_case_threshold field
  - ScoringConfigUpdate accepts ingest_rate_limit_tpm
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../libs"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock(scalar_sequence: list):
    """Return an AsyncMock db that cycles through scalar_sequence per execute call."""
    call_idx = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_idx
        result = MagicMock()
        if call_idx < len(scalar_sequence):
            result.scalar.return_value = scalar_sequence[call_idx]
        else:
            result.scalar.return_value = 0
        call_idx += 1
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


def _make_user(tenant_id: str = "t1", role: str = "ADMIN") -> MagicMock:
    user = MagicMock()
    user.tenant_id = tenant_id
    user.id = "user-123"
    user.role = role
    return user


def _make_api_key(tenant_id: str = "t1", permissions: list | None = None) -> MagicMock:
    key = MagicMock()
    key.tenant_id = tenant_id
    key.id = "key-123"
    key.permissions = permissions if permissions is not None else ["ingest"]
    return key


# ---------------------------------------------------------------------------
# 1–4: Usage stats endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_stats_returns_correct_keys():
    """get_usage_stats must return all 7 expected keys."""
    from routers.admin import get_usage_stats

    db = _make_db_mock([100, 50, 10, 1024 * 1024 * 200])
    user = _make_user()

    result = await get_usage_stats(db=db, current_user=user)

    assert "tenant_id" in result
    assert "period" in result
    assert "events_this_month" in result
    assert "alerts_this_month" in result
    assert "open_cases" in result
    assert "db_size_mb" in result
    assert "minio_mb" in result


@pytest.mark.asyncio
async def test_usage_stats_counts_events_this_month():
    """get_usage_stats must aggregate processed_records from ingest_jobs."""
    from routers.admin import get_usage_stats

    db = _make_db_mock([500, 0, 0, 0])
    user = _make_user()

    result = await get_usage_stats(db=db, current_user=user)
    assert result["events_this_month"] == 500


@pytest.mark.asyncio
async def test_usage_stats_counts_alerts_this_month():
    """get_usage_stats must count alerts created in the current month."""
    from routers.admin import get_usage_stats

    db = _make_db_mock([0, 42, 0, 0])
    user = _make_user()

    result = await get_usage_stats(db=db, current_user=user)
    assert result["alerts_this_month"] == 42


@pytest.mark.asyncio
async def test_usage_stats_counts_open_cases():
    """get_usage_stats must count cases not in CLOSED/REPORTED."""
    from routers.admin import get_usage_stats

    db = _make_db_mock([0, 0, 7, 0])
    user = _make_user()

    result = await get_usage_stats(db=db, current_user=user)
    assert result["open_cases"] == 7


# ---------------------------------------------------------------------------
# 5–8: IngestPrincipal / get_ingest_principal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_principal_with_valid_api_key():
    """get_ingest_principal must return IngestPrincipal with role=API_KEY for valid X-API-Key."""
    from auth import get_ingest_principal

    api_key_obj = _make_api_key(tenant_id="t1", permissions=["ingest"])
    db = AsyncMock()
    tenant = MagicMock()
    tenant.active = True
    tenant.settings = {}
    db.get = AsyncMock(return_value=tenant)

    with patch("auth.validate_api_key", new=AsyncMock(return_value=api_key_obj)):
        principal = await get_ingest_principal(
            authorization=None,
            x_api_key="btml_testhash",
            db=db,
        )

    # RLS tenant context must be set on the active DB session.
    assert db.execute.await_count >= 1
    assert any(
        "set_config('app.current_tenant'" in str(call.args[0])
        for call in db.execute.await_args_list
        if call.args
    )

    assert principal.tenant_id == "t1"
    assert principal.id is None
    assert principal.role == "API_KEY"


@pytest.mark.asyncio
async def test_ingest_principal_with_valid_jwt():
    """get_ingest_principal must return IngestPrincipal with user's id for valid Bearer JWT."""
    from auth import get_ingest_principal

    user = _make_user(tenant_id="t2", role="AML_ANALYST")
    db = AsyncMock()
    tenant = MagicMock()
    tenant.active = True
    tenant.settings = {}
    db.get = AsyncMock(return_value=tenant)

    with patch("auth.get_current_user", new=AsyncMock(return_value=user)):
        principal = await get_ingest_principal(
            authorization="Bearer fake.jwt.token",
            x_api_key=None,
            db=db,
        )

    # Defensive: also sets tenant context when called without middleware.
    assert db.execute.await_count >= 1
    assert any(
        "set_config('app.current_tenant'" in str(call.args[0])
        for call in db.execute.await_args_list
        if call.args
    )

    assert principal.tenant_id == "t2"
    assert principal.id == "user-123"
    assert principal.role == "AML_ANALYST"


@pytest.mark.asyncio
async def test_ingest_principal_api_key_paused_returns_503():
    from fastapi import HTTPException
    from auth import get_ingest_principal

    api_key_obj = _make_api_key(tenant_id="t1", permissions=["ingest"])
    db = AsyncMock()
    tenant = MagicMock()
    tenant.active = True
    tenant.settings = {"ingest_paused": True}
    db.get = AsyncMock(return_value=tenant)

    with patch("auth.validate_api_key", new=AsyncMock(return_value=api_key_obj)):
        with pytest.raises(HTTPException) as exc_info:
            await get_ingest_principal(
                authorization=None,
                x_api_key="btml_testhash",
                db=db,
            )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_ingest_principal_jwt_paused_returns_503():
    from fastapi import HTTPException
    from auth import get_ingest_principal

    user = _make_user(tenant_id="t2", role="AML_ANALYST")
    db = AsyncMock()
    tenant = MagicMock()
    tenant.active = True
    tenant.settings = {"ingest_paused": True}
    db.get = AsyncMock(return_value=tenant)

    with patch("auth.get_current_user", new=AsyncMock(return_value=user)):
        with pytest.raises(HTTPException) as exc_info:
            await get_ingest_principal(
                authorization="Bearer fake.jwt.token",
                x_api_key=None,
                db=db,
            )

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_ingest_principal_missing_auth_raises_401():
    """get_ingest_principal must raise 401 when neither header is provided."""
    from fastapi import HTTPException
    from auth import get_ingest_principal

    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_ingest_principal(
            authorization=None,
            x_api_key=None,
            db=db,
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_ingest_principal_api_key_no_ingest_permission_raises_403():
    """get_ingest_principal must raise 403 when API key lacks 'ingest' permission."""
    from fastapi import HTTPException
    from auth import get_ingest_principal

    api_key_obj = _make_api_key(tenant_id="t1", permissions=["admin"])
    db = AsyncMock()

    with patch("auth.validate_api_key", new=AsyncMock(return_value=api_key_obj)):
        with pytest.raises(HTTPException) as exc_info:
            await get_ingest_principal(
                authorization=None,
                x_api_key="btml_noingesthash",
                db=db,
            )

    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# 9–10: Schema field checks
# ---------------------------------------------------------------------------

def test_scoring_config_out_has_auto_case_threshold_field():
    """ScoringConfigOut must expose auto_case_threshold with default 0.75."""
    from libs.schemas import ScoringConfigOut

    fields = ScoringConfigOut.model_fields
    assert "auto_case_threshold" in fields
    assert fields["auto_case_threshold"].default == 0.75


def test_scoring_config_update_accepts_ingest_rate_limit_tpm():
    """ScoringConfigUpdate must accept ingest_rate_limit_tpm (Optional[int], default None)."""
    from libs.schemas import ScoringConfigUpdate

    fields = ScoringConfigUpdate.model_fields
    assert "ingest_rate_limit_tpm" in fields

    update = ScoringConfigUpdate(ingest_rate_limit_tpm=500)
    assert update.ingest_rate_limit_tpm == 500
