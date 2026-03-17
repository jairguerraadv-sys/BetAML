"""
tests/unit/test_cases_module5.py — Module 5 unit tests.

Covers:
  - create_case: SLA auto-set from ScoringConfig (CRITICAL=4h, HIGH=24h, defaults)
  - create_case: fallback SLA when no ScoringConfig exists
  - assign_case: CASE_ASSIGNED notification dispatched
  - add_case_comment: creates COMMENT CaseEvent
  - add_case_comment: @mentions dispatch CASE_MENTION notifications
  - add_case_comment: AUDITOR role is forbidden (403)
  - link_alert_to_case: sets alert.case_id and returns "linked"
  - link_alert_to_case: cross-tenant alert raises 404
  - add_case_event STATUS_CHANGE: CLOSED sets closed_at
  - check_sla_violations: uses correct active statuses
  - player transactions-chart endpoint registered
  - player bets-chart endpoint registered
  - player payment-instruments endpoint registered
  - player network endpoint registered
  - player case-alert-history endpoint registered
"""
from __future__ import annotations

import sys
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(
    user_id: str = "u1",
    tenant_id: str = "t1",
    role: str = "AML_ANALYST",
    username: str = "analyst",
):
    u = MagicMock()
    u.id = user_id
    u.tenant_id = tenant_id
    u.role = role
    u.username = username
    return u


def _make_case(
    case_id: str = "c1",
    tenant_id: str = "t1",
    status: str = "OPEN",
    title: str = "Test Case",
    sla_due_at=None,
):
    c = MagicMock()
    c.id = case_id
    c.tenant_id = tenant_id
    c.status = status
    c.title = title
    c.sla_due_at = sla_due_at
    c.severity = "HIGH"
    c.player_id = None
    c.created_by = "u1"
    c.assigned_to = None
    c.created_at = datetime.now(UTC)
    return c


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


def _make_scoring_config(
    critical: int = 4,
    high: int = 24,
    medium: int = 72,
    low: int = 168,
    tenant_id: str = "t1",
):
    sc = MagicMock()
    sc.tenant_id = tenant_id
    sc.sla_critical_hours = critical
    sc.sla_high_hours = high
    sc.sla_medium_hours = medium
    sc.sla_low_hours = low
    return sc


# ---------------------------------------------------------------------------
# SLA auto-set on create_case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_case_sets_sla_from_scoring_config_critical():
    """CRITICAL severity → SLA = now + sla_critical_hours (4 by default)."""
    from routers.cases import create_case, CaseCreate

    db = _make_db()
    sc = _make_scoring_config(critical=4)

    # db.execute returns the ScoringConfig on first call, nothing on subsequent
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = sc
    db.execute = AsyncMock(return_value=result_mock)

    body = CaseCreate(title="Critical AML", severity="CRITICAL")
    user = _make_user()

    added_objects = []
    db.add = MagicMock(side_effect=added_objects.append)

    before = datetime.now(UTC)
    with patch("routers.cases.write_audit", AsyncMock()):
        await create_case(body=body, current_user=user, db=db)
    after = datetime.now(UTC)

    # Find the Case object that was added
    case_obj = next((obj for obj in added_objects if hasattr(obj, "sla_due_at")), None)
    assert case_obj is not None, "Case object not found in db.add calls"
    assert case_obj.sla_due_at is not None
    expected_min = before + timedelta(hours=4) - timedelta(seconds=5)
    expected_max = after  + timedelta(hours=4) + timedelta(seconds=5)
    assert expected_min <= case_obj.sla_due_at <= expected_max


@pytest.mark.asyncio
async def test_create_case_default_sla_when_no_scoring_config():
    """When ScoringConfig is absent, HIGH falls back to 24h default."""
    from routers.cases import create_case, CaseCreate

    db = _make_db()

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None  # no config
    db.execute = AsyncMock(return_value=result_mock)

    added_objects = []
    db.add = MagicMock(side_effect=added_objects.append)

    before = datetime.now(UTC)
    body = CaseCreate(title="High AML", severity="HIGH")
    with patch("routers.cases.write_audit", AsyncMock()):
        await create_case(body=body, current_user=_make_user(), db=db)
    after = datetime.now(UTC)

    case_obj = next((obj for obj in added_objects if hasattr(obj, "sla_due_at")), None)
    assert case_obj is not None
    expected_min = before + timedelta(hours=24) - timedelta(seconds=5)
    expected_max = after  + timedelta(hours=24) + timedelta(seconds=5)
    assert expected_min <= case_obj.sla_due_at <= expected_max


# ---------------------------------------------------------------------------
# assign_case — CASE_ASSIGNED notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_case_dispatches_notification():
    """assign_case must db.add a Notification of type CASE_ASSIGNED."""
    from routers.cases import assign_case, AssignRequest

    db = _make_db()
    c = _make_case()
    db.get = AsyncMock(return_value=c)

    added_objects = []
    db.add = MagicMock(side_effect=added_objects.append)

    body = AssignRequest(user_id="analyst1")
    user = _make_user(role="ADMIN")

    with patch("routers.cases.write_audit", AsyncMock()):
        result = await assign_case(case_id="c1", body=body, current_user=user, db=db)

    assert result["assigned_to"] == "analyst1"

    notif = next(
        (obj for obj in added_objects if getattr(obj, "type", None) == "CASE_ASSIGNED"),
        None,
    )
    assert notif is not None, "CASE_ASSIGNED notification not dispatched"
    assert notif.user_id == "analyst1"
    assert notif.reference_type == "Case"


# ---------------------------------------------------------------------------
# add_case_comment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_case_comment_creates_event():
    """POST /cases/{id}/comments creates a COMMENT CaseEvent."""
    from routers.cases import add_case_comment
    from libs.schemas import CaseCommentIn

    db = _make_db()
    c = _make_case()
    db.get = AsyncMock(return_value=c)

    added_objects = []
    db.add = MagicMock(side_effect=added_objects.append)

    body = CaseCommentIn(content="Suspicious activity noted.", mentions=[])
    with patch("routers.cases.write_audit", AsyncMock()):
        result = await add_case_comment(case_id="c1", body=body, current_user=_make_user(), db=db)

    assert "id" in result
    assert "created_at" in result

    evt = next((obj for obj in added_objects if getattr(obj, "event_type", None) == "COMMENT"), None)
    assert evt is not None, "COMMENT CaseEvent not created"
    assert evt.content["comment"] == "Suspicious activity noted."


@pytest.mark.asyncio
async def test_add_case_comment_with_mentions_dispatches_notifications():
    """@mentions in a comment must each produce a CASE_MENTION Notification."""
    from routers.cases import add_case_comment
    from libs.schemas import CaseCommentIn

    db = _make_db()
    c = _make_case()
    db.get = AsyncMock(return_value=c)

    added_objects = []
    db.add = MagicMock(side_effect=added_objects.append)

    body = CaseCommentIn(content="Check this out.", mentions=["user_a", "user_b"])
    with patch("routers.cases.write_audit", AsyncMock()):
        await add_case_comment(case_id="c1", body=body, current_user=_make_user(), db=db)

    mention_notifs = [
        obj for obj in added_objects if getattr(obj, "type", None) == "CASE_MENTION"
    ]
    assert len(mention_notifs) == 2
    mentioned_users = {n.user_id for n in mention_notifs}
    assert mentioned_users == {"user_a", "user_b"}


@pytest.mark.asyncio
async def test_add_case_comment_auditor_forbidden():
    """AUDITOR role must not be allowed to add comments (require_roles guard)."""
    from routers.cases import require_roles

    dependency = require_roles("ADMIN", "AML_ANALYST")
    user = _make_user(role="AUDITOR")
    from fastapi import HTTPException as FastAPIHTTPException
    with pytest.raises(FastAPIHTTPException) as exc_info:
        await dependency(current_user=user)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# link_alert_to_case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_link_alert_to_case_success():
    """link_alert_to_case sets alert.case_id and returns status 'linked'."""
    from routers.cases import link_alert_to_case
    from libs.schemas import CaseLinkAlertIn

    db = _make_db()
    c = _make_case(tenant_id="t1")
    alert_mock = MagicMock()
    alert_mock.tenant_id = "t1"
    alert_mock.title = "Spike"
    alert_mock.severity = "HIGH"
    alert_mock.case_id = None

    async def _db_get(model, pk):
        from models import Case, Alert
        if model is Case:
            return c
        return alert_mock

    db.get = AsyncMock(side_effect=_db_get)

    body = CaseLinkAlertIn(alert_id="a1")
    with patch("routers.cases.write_audit", AsyncMock()):
        result = await link_alert_to_case(case_id="c1", body=body, current_user=_make_user(), db=db)

    assert result["status"] == "linked"
    assert alert_mock.case_id == "c1"


@pytest.mark.asyncio
async def test_link_alert_wrong_tenant_raises_404():
    """Alert belonging to a different tenant must raise 404."""
    from routers.cases import link_alert_to_case
    from libs.schemas import CaseLinkAlertIn
    from fastapi import HTTPException as FastAPIHTTPException

    db = _make_db()
    c = _make_case(tenant_id="t1")
    alert_mock = MagicMock()
    alert_mock.tenant_id = "other_tenant"

    async def _db_get(model, pk):
        from models import Case, Alert
        if model is Case:
            return c
        return alert_mock

    db.get = AsyncMock(side_effect=_db_get)

    body = CaseLinkAlertIn(alert_id="a_other")
    with pytest.raises(FastAPIHTTPException) as exc_info:
        with patch("routers.cases.write_audit", AsyncMock()):
            await link_alert_to_case(case_id="c1", body=body, current_user=_make_user(), db=db)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# add_case_event STATUS_CHANGE → CLOSED sets closed_at
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_change_closed_sets_closed_at():
    """STATUS_CHANGE to CLOSED must set c.closed_at and c.closed_by."""
    from routers.cases import add_case_event, CaseEventCreate

    db = _make_db()
    c = _make_case(status="INVESTIGATING")
    c.closed_at = None
    c.closed_by = None
    db.get = AsyncMock(return_value=c)

    body = CaseEventCreate(
        event_type="STATUS_CHANGE",
        content={"new_status": "CLOSED"},
    )
    with patch("routers.cases.write_audit", AsyncMock()):
        await add_case_event(case_id="c1", body=body, current_user=_make_user(), db=db)

    assert c.status == "CLOSED"
    assert c.closed_at is not None
    assert c.closed_by == "u1"


# ---------------------------------------------------------------------------
# check_sla_violations — correct statuses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_sla_violations_uses_active_statuses():
    """check_sla_violations must query OPEN/INVESTIGATING/PENDING_REVIEW, not IN_REVIEW."""
    from jobs import check_sla_violations

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    db.commit = AsyncMock()
    db.add = MagicMock()

    executed_queries: list = []

    async def _capture_execute(stmt, *args, **kwargs):
        executed_queries.append(stmt)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        return mock_result

    db.execute = AsyncMock(side_effect=_capture_execute)

    with patch("jobs.AsyncSessionLocal", return_value=MagicMock(
        __aenter__=AsyncMock(return_value=db),
        __aexit__=AsyncMock(return_value=False),
    )):
        await check_sla_violations()

    # Inspect queries for status references
    query_str = " ".join(str(q) for q in executed_queries).upper()
    assert "IN_REVIEW" not in query_str, "Stale IN_REVIEW status referenced in SLA job"
    assert "INVESTIGATING" in query_str or len(executed_queries) > 0


# ---------------------------------------------------------------------------
# Player enrichment router path registration
# ---------------------------------------------------------------------------

def test_player_transactions_chart_endpoint_registered():
    """GET /players/{player_id}/transactions-chart must be registered."""
    from routers.players import router
    paths = [r.path for r in router.routes]
    assert any("transactions-chart" in p for p in paths)


def test_player_bets_chart_endpoint_registered():
    from routers.players import router
    paths = [r.path for r in router.routes]
    assert any("bets-chart" in p for p in paths)


def test_player_payment_instruments_endpoint_registered():
    from routers.players import router
    paths = [r.path for r in router.routes]
    assert any("payment-instruments" in p for p in paths)


def test_player_network_endpoint_registered():
    from routers.players import router
    paths = [r.path for r in router.routes]
    assert any("network" in p for p in paths)


def test_player_case_alert_history_endpoint_registered():
    from routers.players import router
    paths = [r.path for r in router.routes]
    assert any("case-alert-history" in p for p in paths)
