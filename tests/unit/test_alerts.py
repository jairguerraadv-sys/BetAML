"""
tests/unit/test_alerts.py — Unit tests for routers/alerts.py

Tests cover:
  - TriageRequest / LinkCaseRequest / AlertLabelIn schema validation
  - list_alerts: total + items shape, page/per_page pagination
  - get_alert: 404 not-found, 404 wrong-tenant, response shape
  - triage_alert: 404, disposition applied
  - close_alert: 404, status set to CLOSED
  - link_alert_to_case: 404 alert, 404 case, success
  - get_alert_related_transactions: 404, no player_id early exit, with player_id
  - label_alert: 404, success, forbidden for AUDITOR
  - Router registration
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = tenant_id
    u.role = role
    u.roles = None  # Simula usuário legado; get_effective_roles usa o campo `role`
    return u


def _make_db(get_result=None):
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    db.get = AsyncMock(return_value=get_result)
    return db


def _make_alert(tenant_id: str = "t1", alert_id: str = "a1", player_id=None):
    a = MagicMock()
    a.id = alert_id
    a.tenant_id = tenant_id
    a.status = "OPEN"
    a.severity = "HIGH"
    a.title = "Test Alert"
    a.description = "suspicious activity"
    a.alert_type = "AML_SUSPICIOUS"
    a.evidence = {}
    a.player_id = player_id
    a.rule_id = "r1"
    a.anomaly_score = 0.87
    a.source_event_id = "se1"
    a.case_id = None
    a.created_at = datetime(2024, 1, 1, 12, 0, 0)  # naive datetime
    a.label = None
    a.label_note = None
    a.labeled_by = None
    a.labeled_at = None
    a.triaged_by = None
    a.triaged_at = None
    return a


def _make_repo(alerts=None, count=0):
    repo = MagicMock()
    repo.list_filtered = AsyncMock(return_value=alerts or [])
    repo.count_filtered = AsyncMock(return_value=count)
    repo.list_open_recent = AsyncMock(return_value=alerts or [])
    return repo


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_triage_request_default_disposition():
    from routers.alerts import TriageRequest
    req = TriageRequest()
    assert req.disposition == "IN_REVIEW"
    assert req.note is None


def test_triage_request_custom_disposition():
    from routers.alerts import TriageRequest
    req = TriageRequest(disposition="CONFIRMED", note="reviewed")
    assert req.disposition == "CONFIRMED"
    assert req.note == "reviewed"


def test_link_case_request_schema():
    from routers.alerts import LinkCaseRequest
    req = LinkCaseRequest(case_id="c-123")
    assert req.case_id == "c-123"


def test_alert_label_in_valid():
    from routers.alerts import AlertLabelIn
    body = AlertLabelIn(label="TRUE_POSITIVE")
    assert body.label == "TRUE_POSITIVE"
    assert body.label_note is None


def test_alert_label_in_with_note():
    from routers.alerts import AlertLabelIn
    body = AlertLabelIn(label="FALSE_POSITIVE", label_note="not suspicious")
    assert body.label_note == "not suspicious"


# ---------------------------------------------------------------------------
# list_alerts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_alerts_returns_total_and_items():
    from routers.alerts import list_alerts
    alert = _make_alert()
    repo = _make_repo(alerts=[alert], count=1)
    user = _make_user()

    # Pass explicit values to bypass FastAPI Query() defaults (including page/per_page)
    result = await list_alerts(limit=50, offset=0, page=None, per_page=None, current_user=user, repo=repo)

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "a1"


@pytest.mark.asyncio
async def test_list_alerts_empty():
    from routers.alerts import list_alerts
    repo = _make_repo(alerts=[], count=0)
    user = _make_user()

    result = await list_alerts(limit=50, offset=0, page=None, per_page=None, current_user=user, repo=repo)

    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_list_alerts_items_have_expected_keys():
    from routers.alerts import list_alerts
    alert = _make_alert()
    repo = _make_repo(alerts=[alert], count=1)
    user = _make_user()

    result = await list_alerts(limit=50, offset=0, page=None, per_page=None, current_user=user, repo=repo)

    item = result["items"][0]
    for key in ("id", "severity", "status", "title", "alert_type", "player_id", "case_id"):
        assert key in item


@pytest.mark.asyncio
async def test_list_alerts_page_per_page_pagination():
    """page=2, per_page=10 → offset=10, limit=10."""
    from routers.alerts import list_alerts
    repo = _make_repo()
    user = _make_user()

    # Pass page/per_page together with explicit limit/offset to bypass Query() defaults
    await list_alerts(page=2, per_page=10, limit=50, offset=0, status_filter=None, severity=None, player_id=None, rule_id=None, current_user=user, repo=repo)

    call_kwargs = repo.list_filtered.call_args[1]
    assert call_kwargs["limit"] == 10
    assert call_kwargs["offset"] == 10


# ---------------------------------------------------------------------------
# get_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_alert_404_not_found():
    from routers.alerts import get_alert
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await get_alert(alert_id="nonexistent", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_alert_404_wrong_tenant():
    from routers.alerts import get_alert
    from fastapi import HTTPException

    alert = _make_alert(tenant_id="other-tenant")
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    with pytest.raises(HTTPException) as exc:
        await get_alert(alert_id="a1", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_alert_found_has_expected_keys():
    from routers.alerts import get_alert

    alert = _make_alert(tenant_id="t1")
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    result = await get_alert(alert_id="a1", current_user=user, db=db)

    for key in ("id", "severity", "status", "title", "description", "alert_type",
                "player_id", "case_id", "created_at"):
        assert key in result


# ---------------------------------------------------------------------------
# triage_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triage_alert_404():
    from routers.alerts import triage_alert, TriageRequest
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await triage_alert(alert_id="x", body=TriageRequest(), current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_triage_alert_applies_disposition():
    from routers.alerts import triage_alert, TriageRequest

    alert = _make_alert(tenant_id="t1")
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    with patch("routers.alerts.write_audit", new_callable=AsyncMock):
        result = await triage_alert(
            alert_id="a1",
            body=TriageRequest(disposition="CONFIRMED"),
            current_user=user,
            db=db,
        )

    assert result["status"] == "CONFIRMED"
    assert alert.status == "CONFIRMED"
    assert alert.triaged_by == "u1"


# ---------------------------------------------------------------------------
# close_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_alert_404():
    from routers.alerts import close_alert
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await close_alert(alert_id="x", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_close_alert_sets_closed():
    from routers.alerts import close_alert

    alert = _make_alert(tenant_id="t1")
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    with patch("routers.alerts.write_audit", new_callable=AsyncMock):
        result = await close_alert(alert_id="a1", current_user=user, db=db)

    assert result["status"] == "CLOSED"
    assert alert.status == "CLOSED"


# ---------------------------------------------------------------------------
# link_alert_to_case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_link_alert_to_case_alert_404():
    from routers.alerts import link_alert_to_case, LinkCaseRequest
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await link_alert_to_case(
            alert_id="x",
            body=LinkCaseRequest(case_id="c1"),
            current_user=user,
            db=db,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_link_alert_to_case_case_404():
    from routers.alerts import link_alert_to_case, LinkCaseRequest
    from fastapi import HTTPException

    alert = _make_alert(tenant_id="t1")
    # First db.get returns alert, second returns None (case not found)
    db = _make_db()
    db.get = AsyncMock(side_effect=[alert, None])
    user = _make_user(tenant_id="t1")

    with pytest.raises(HTTPException) as exc:
        await link_alert_to_case(
            alert_id="a1",
            body=LinkCaseRequest(case_id="c1"),
            current_user=user,
            db=db,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_link_alert_to_case_success():
    from routers.alerts import link_alert_to_case, LinkCaseRequest

    alert = _make_alert(tenant_id="t1")
    case_mock = MagicMock()
    case_mock.tenant_id = "t1"

    db = _make_db()
    db.get = AsyncMock(side_effect=[alert, case_mock])
    user = _make_user(tenant_id="t1")

    with patch("routers.alerts.write_audit", new_callable=AsyncMock):
        result = await link_alert_to_case(
            alert_id="a1",
            body=LinkCaseRequest(case_id="c-99"),
            current_user=user,
            db=db,
        )

    assert result["alert_id"] == "a1"
    assert result["case_id"] == "c-99"
    assert alert.case_id == "c-99"


# ---------------------------------------------------------------------------
# get_alert_related_transactions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_related_transactions_404():
    from routers.alerts import get_alert_related_transactions
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await get_alert_related_transactions(alert_id="x", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_related_transactions_no_player_returns_empty():
    from routers.alerts import get_alert_related_transactions

    alert = _make_alert(tenant_id="t1", player_id=None)
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    result = await get_alert_related_transactions(alert_id="a1", current_user=user, db=db)

    assert result["transactions"] == []
    assert result["bets"] == []
    assert result["alert_id"] == "a1"


@pytest.mark.asyncio
async def test_get_related_transactions_with_player_returns_shape():
    from routers.alerts import get_alert_related_transactions

    alert = _make_alert(tenant_id="t1", player_id="P-99")
    # Use naive datetime to avoid tzinfo branching issues
    alert.created_at = datetime(2024, 6, 1, 12, 0, 0)
    db = _make_db(get_result=alert)
    user = _make_user(tenant_id="t1")

    # Pass explicit window_hours/limit to bypass FastAPI Query() defaults
    result = await get_alert_related_transactions(
        alert_id="a1", window_hours=48, limit=50, current_user=user, db=db
    )

    assert "transactions" in result
    assert "bets" in result
    assert result["player_id"] == "P-99"
    assert result["window_hours"] == 48  # default


# ---------------------------------------------------------------------------
# label_alert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_label_alert_404():
    from routers.alerts import label_alert, AlertLabelIn
    from fastapi import BackgroundTasks, HTTPException

    db = _make_db()  # scalar_one_or_none returns None by default
    user = _make_user()
    body = AlertLabelIn(label="TRUE_POSITIVE")
    bg = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        await label_alert(alert_id="x", body=body, background_tasks=bg, db=db, current_user=user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_label_alert_success():
    from routers.alerts import label_alert, AlertLabelIn
    from fastapi import BackgroundTasks

    alert = _make_alert(tenant_id="t1")
    db = _make_db()

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = alert
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    user = _make_user(tenant_id="t1")
    body = AlertLabelIn(label="FALSE_POSITIVE", label_note="reviewed manually")
    bg = BackgroundTasks()

    with patch("routers.alerts.write_audit", new_callable=AsyncMock):
        result = await label_alert(alert_id="a1", body=body, background_tasks=bg, db=db, current_user=user)

    assert result["status"] == "labeled"
    assert result["label"] == "FALSE_POSITIVE"
    assert alert.label == "FALSE_POSITIVE"
    assert alert.label_note == "reviewed manually"


@pytest.mark.asyncio
async def test_label_alert_forbidden_for_auditor():
    from routers.alerts import label_alert, AlertLabelIn
    from fastapi import BackgroundTasks, HTTPException

    alert = _make_alert(tenant_id="t1")
    db = _make_db()

@pytest.mark.asyncio
async def test_label_alert_auditor_can_label_after_rbac_migration():
    """AUDITOR (legado) agora mapeia para Operador_Analista e PODE rotular alertas.

    No RBAC refatorado, AUDITOR → Operador_Analista via _LEGACY_ROLE_MAP.
    Analistas têm permissão alerts:rw, portanto podem rotular.
    Para um papel somente-leitura futuro, use uma conta com papel específico.
    """
    from routers.alerts import label_alert, AlertLabelIn
    from fastapi import BackgroundTasks

    alert = _make_alert(tenant_id="t1")
    alert.label = None
    db = _make_db()

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = alert
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    user = _make_user(tenant_id="t1", role="AUDITOR")
    body = AlertLabelIn(label="FALSE_POSITIVE", label_note="auditor can label now")
    bg = BackgroundTasks()

    with patch("routers.alerts.write_audit", new_callable=AsyncMock):
        result = await label_alert(alert_id="a1", body=body, background_tasks=bg, db=db, current_user=user)

    assert result["status"] == "labeled"
    assert alert.label == "FALSE_POSITIVE"


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def test_alerts_router_has_list_endpoint():
    from routers.alerts import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/alerts" in paths


def test_alerts_router_has_triage_endpoint():
    from routers.alerts import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/alerts/{alert_id}/triage" in paths


def test_alerts_router_has_label_endpoint():
    from routers.alerts import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/alerts/{alert_id}/label" in paths
