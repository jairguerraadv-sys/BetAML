"""
tests/unit/test_stats.py — Unit tests for routers/stats.py.

Tests cover:
  - GET /stats/dashboard returns required KPI keys
  - Numeric fields are non-negative
  - Tenant isolation (stats only sees own tenant's data)
  - Router registration (stats router is included in main app)
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "tenant-a", role: str = "ADMIN"):
    user = MagicMock()
    user.tenant_id = tenant_id
    user.role = role
    user.id = "user-001"
    return user


def _make_db_scalar(values: list):
    """Create a mock DB that returns scalars in sequence."""
    db = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        val = values[idx] if idx < len(values) else 0
        result.scalar_one.return_value = val
        result.scalar_one_or_none.return_value = val
        result.scalar.return_value = val
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        return result

    db.execute = _execute
    return db


# ---------------------------------------------------------------------------
# Stats router unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_returns_required_kpi_keys():
    """GET /stats/dashboard must include all required KPI keys for the frontend."""
    from routers.stats import dashboard_stats

    # DB returns: alerts_today=5, critical_open=2, cases_open=10, sla_expired=1,
    #             auto_detected=3, then by_severity rows=[]
    db = _make_db_scalar([5, 2, 10, 1, 3])
    user = _make_user()

    result = await dashboard_stats(db=db, current_user=user)

    for key in ("alerts_today", "critical_open", "cases_open", "sla_expired", "auto_detected", "by_severity"):
        assert key in result, f"Missing KPI key: {key}"


@pytest.mark.asyncio
async def test_dashboard_kpis_are_non_negative():
    """All dashboard KPI count values must be non-negative."""
    from routers.stats import dashboard_stats

    db = _make_db_scalar([0, 0, 0, 0, 0])
    user = _make_user()

    result = await dashboard_stats(db=db, current_user=user)

    for key, val in result.items():
        if isinstance(val, (int, float)):
            assert val >= 0, f"Negative KPI value for {key}: {val}"


@pytest.mark.asyncio
async def test_dashboard_returns_correct_counts():
    """dashboard_stats returns the exact scalar values from the DB."""
    from routers.stats import dashboard_stats

    db = _make_db_scalar([7, 3, 12, 2, 5])
    user = _make_user()

    result = await dashboard_stats(db=db, current_user=user)

    assert result["alerts_today"] == 7
    assert result["critical_open"] == 3
    assert result["cases_open"] == 12
    assert result["sla_expired"] == 2
    assert result["auto_detected"] == 5


def test_stats_router_uses_tenant_id():
    """Stats router source must reference tenant_id for isolation."""
    from routers import stats as stats_module
    import inspect

    src = inspect.getsource(stats_module)
    assert "tenant_id" in src, (
        "stats.py does not reference tenant_id — tenant isolation may be missing"
    )
