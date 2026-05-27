"""
tests/unit/test_reports.py — Unit tests for routers/reports.py.

Tests cover:
  - _build_monthly_report aggregation helper (mocked DB)
  - GET /reports/monthly-summary date validation
  - POST /reports/monthly-summary enqueues background task
  - Duplicate PDF route (GAP-3) must NOT be registered in reports router
  - GAP-4 RBAC: AUDITOR can GET but cannot POST monthly-summary
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(overrides: dict | None = None):
    """Return a minimal mocked AsyncSession."""
    db = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        data = (overrides or {}).get(idx)
        if data is None:
            # Default: return empty aggregations
            result.all.return_value = []
            result.scalar_one.return_value = 0
            result.scalar.return_value = 0
        else:
            for k, v in data.items():
                setattr(result, k, v)
        return result

    db.execute = _execute
    return db


# ---------------------------------------------------------------------------
# _build_monthly_report unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_monthly_report_returns_expected_keys():
    """_build_monthly_report always returns the required top-level keys."""
    from routers.reports import _build_monthly_report

    db = _make_db()
    date_from = datetime(2026, 1, 1, tzinfo=UTC)
    date_to = datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC)

    result = await _build_monthly_report("tenant-x", date_from, date_to, db)

    assert "period" in result
    assert "alerts_by_severity" in result
    assert "cases_summary" in result
    assert "top_rules_by_fires" in result
    assert "top_players_by_risk" in result
    assert "total_ingested_events" in result
    assert "total_communications_generated" in result
    assert "quality_metrics" in result
    assert "false_positive_rate" in result
    assert "true_positive_rate" in result
    assert "generated_at" in result


@pytest.mark.asyncio
async def test_build_monthly_report_defaults_zero_counts():
    """With empty DB, all counts default to 0 and false_positive_rate is None."""
    from routers.reports import _build_monthly_report

    db = _make_db()
    date_from = datetime(2026, 2, 1, tzinfo=UTC)
    date_to = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)

    result = await _build_monthly_report("tenant-y", date_from, date_to, db)

    assert result["alerts_by_severity"]["CRITICAL"] == 0
    assert result["total_ingested_events"] == 0
    assert result["total_communications_generated"] == 0
    assert result["false_positive_rate"] is None


@pytest.mark.asyncio
async def test_build_monthly_report_period_preserved():
    """Period from/to is reflected correctly in the returned payload."""
    from routers.reports import _build_monthly_report

    db = _make_db()
    date_from = datetime(2026, 3, 1, tzinfo=UTC)
    date_to = datetime(2026, 3, 31, tzinfo=UTC)

    result = await _build_monthly_report("t", date_from, date_to, db)

    assert result["period"]["from"].startswith("2026-03-01")
    assert result["period"]["to"].startswith("2026-03-31")


# ---------------------------------------------------------------------------
# GAP-3: Duplicate PDF route must NOT be registered in reports router
# ---------------------------------------------------------------------------

def test_reports_router_has_no_pdf_download_route():
    """GAP-3: GET /cases/{case_id}/report-package/pdf must NOT exist in reports.router."""
    from routers.reports import router

    pdf_routes = [
        r for r in router.routes
        if hasattr(r, "path") and "report-package/pdf" in r.path
    ]
    assert len(pdf_routes) == 0, (
        "Duplicate PDF route detected in reports.router — violates GAP-3. "
        "Route should only exist in cases.router with proper RBAC."
    )


# ---------------------------------------------------------------------------
# GAP-4: RBAC checks on monthly summary
# ---------------------------------------------------------------------------

def test_reports_generate_monthly_requires_admin_analyst():
    """GAP-4: POST /reports/monthly-summary must use require_roles, not get_current_user."""
    from routers.reports import generate_monthly_report
    import inspect

    sig = inspect.signature(generate_monthly_report)
    current_user_param = sig.parameters.get("current_user")
    assert current_user_param is not None

    dep = current_user_param.default
    # The dependency should be a Depends() wrapping require_roles, not raw get_current_user
    dep_repr = repr(dep)
    assert "require_roles" in dep_repr or "get_current_user" not in dep_repr, (
        "POST /reports/monthly-summary should use require_roles('ADMIN','AML_ANALYST'), not bare get_current_user"
    )


@pytest.mark.asyncio
async def test_get_monthly_summary_rejects_invalid_date_format():
    from routers.reports import get_monthly_summary

    db = AsyncMock()
    user = SimpleNamespace(id="u-1", tenant_id="tenant-1")

    with pytest.raises(HTTPException) as exc_info:
        await get_monthly_summary(
            date_from="2026/01/01",
            date_to="2026-01-31",
            db=db,
            current_user=user,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_monthly_summary_rejects_reversed_dates():
    from routers.reports import get_monthly_summary

    db = AsyncMock()
    user = SimpleNamespace(id="u-1", tenant_id="tenant-1")

    with pytest.raises(HTTPException) as exc_info:
        await get_monthly_summary(
            date_from="2026-02-10",
            date_to="2026-02-01",
            db=db,
            current_user=user,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_monthly_summary_csv_returns_streaming_response():
    from routers.reports import get_monthly_summary_csv

    db = AsyncMock()
    db.commit = AsyncMock()
    user = SimpleNamespace(id="u-1", tenant_id="tenant-1")
    fake_report = {
        "period": {"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-31T23:59:59+00:00"},
        "generated_at": "2026-01-31T23:59:59+00:00",
        "alerts_by_severity": {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4},
        "cases_summary": {"open": 1, "investigating": 2, "closed": 3, "reported": 4},
        "total_ingested_events": 10,
        "total_alerts": 10,
        "total_cases": 10,
        "total_cases_opened": 1,
        "total_cases_closed": 2,
        "total_cases_reported": 3,
        "total_communications_generated": 4,
        "total_sar_reports": 5,
        "false_positive_rate": 0.1,
        "true_positive_rate": 90.0,
        "top_rules_by_fires": [],
        "top_players_by_risk": [],
        "quality_metrics": {
            "labeled_alerts": 10,
            "true_positive_count": 9,
            "false_positive_count": 1,
            "unknown_count": 0,
            "true_positive_rate": 90.0,
            "false_positive_rate": 0.1,
        },
    }

    with patch("routers.reports._build_monthly_report", new_callable=AsyncMock, return_value=fake_report), patch(
        "routers.reports.write_audit", new_callable=AsyncMock
    ):
        response = await get_monthly_summary_csv(
            date_from="2026-01-01",
            date_to="2026-01-31",
            db=db,
            current_user=user,
        )

    assert response.media_type == "text/csv; charset=utf-8-sig"
    assert "monthly_summary_2026-01-01_2026-01-31.csv" in response.headers["Content-Disposition"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_monthly_report_queues_background_task():
    from routers.reports import MonthlyReportIn, generate_monthly_report

    db = AsyncMock()
    db.commit = AsyncMock()
    tasks = BackgroundTasks()
    user = SimpleNamespace(id="u-1", tenant_id="tenant-1")

    with patch("routers.reports.write_audit", new_callable=AsyncMock):
        response = await generate_monthly_report(
            body=MonthlyReportIn(year=2026, month=3),
            background_tasks=tasks,
            db=db,
            current_user=user,
        )

    assert response == {"status": "queued", "year": 2026, "month": 3}
    assert len(tasks.tasks) == 1
    db.commit.assert_awaited_once()
