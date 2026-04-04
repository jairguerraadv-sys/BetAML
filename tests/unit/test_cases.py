"""
tests/unit/test_cases.py — Unit tests for routers/cases.py.

Tests cover:
  - _STATUS_TRANSITIONS graph completeness and terminal states
  - _build_report_pdf raises RuntimeError when reportlab missing
  - create_case: DB flush+commit called, response keys present
  - list_cases: delegates to CaseRepository and returns list
  - get_case: 404 when not found
  - add_case_event: valid status transition succeeds
  - add_case_event: invalid transition on terminal REPORTED raises 400
  - submit_report_package: 400 when no ReportPackage exists
  - submit_report_package: 400 when decision is not FILE_SAR
  - submit_report_package: 409 when already FILED
  - tenant isolation: cross-tenant access blocked on get_case
"""
from __future__ import annotations

import sys
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str = "u1", tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = user_id
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_case(
    case_id: str = "c1",
    tenant_id: str = "t1",
    status: str = "OPEN",
    player_id: str | None = None,
):
    c = MagicMock()
    c.id = case_id
    c.tenant_id = tenant_id
    c.title = "Test Case"
    c.description = "Description"
    c.status = status
    c.severity = "HIGH"
    c.player_id = player_id
    c.created_by = "u1"
    c.assigned_to = None
    c.created_at = datetime.now(UTC)
    c.reference_number = None
    c.priority = "MEDIUM"
    c.sla_due_at = None
    return c


def _make_db() -> AsyncMock:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    # Ensure execute(..).scalar_one_or_none() returns None (ScoringConfig lookup → fallback)
    _exec_result = MagicMock()
    _exec_result.scalar_one_or_none = MagicMock(return_value=None)
    _exec_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=_exec_result)
    return db


# ---------------------------------------------------------------------------
# _STATUS_TRANSITIONS graph
# ---------------------------------------------------------------------------

def test_status_transitions_reported_is_terminal():
    """REPORTED must have no outbound transitions (terminal state for COAF submission)."""
    from routers.cases import _STATUS_TRANSITIONS

    assert _STATUS_TRANSITIONS["REPORTED"] == [], "REPORTED must be terminal"


def test_status_transitions_open_can_investigate():
    """OPEN → INVESTIGATING is a required transition in the AML workflow."""
    from routers.cases import _STATUS_TRANSITIONS

    assert "INVESTIGATING" in _STATUS_TRANSITIONS["OPEN"]


def test_status_transitions_all_statuses_have_entries():
    """Every status that can appear must have an entry in the transition graph."""
    from routers.cases import _STATUS_TRANSITIONS

    expected = {"OPEN", "INVESTIGATING", "PENDING_REVIEW", "CLOSED", "REPORTED"}
    assert set(_STATUS_TRANSITIONS.keys()) == expected


def test_status_transitions_closed_can_reopen():
    """CLOSED → OPEN is allowed (re-opening a case)."""
    from routers.cases import _STATUS_TRANSITIONS

    assert "OPEN" in _STATUS_TRANSITIONS["CLOSED"]


# ---------------------------------------------------------------------------
# _build_report_pdf
# ---------------------------------------------------------------------------

def test_build_report_pdf_raises_on_missing_reportlab():
    """_build_report_pdf must raise RuntimeError when reportlab is not installed."""
    from routers.cases import _build_report_pdf
    import builtins, importlib

    real_import = builtins.__import__

    def _block_reportlab(name, *args, **kwargs):
        if name.startswith("reportlab"):
            raise ImportError("simulated missing reportlab")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_block_reportlab):
        with pytest.raises(RuntimeError, match="reportlab"):
            _build_report_pdf({"report_id": "x", "generated_at": "2026-01-01"})


# ---------------------------------------------------------------------------
# create_case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_case_calls_flush_and_commit():
    """POST /cases must flush (get id) then commit; returns id/title/status."""
    from routers.cases import create_case, CaseCreate

    db = _make_db()
    body = CaseCreate(title="Structuring Investigation", severity="HIGH")
    user = _make_user()

    with patch("routers.cases.write_audit", AsyncMock()):
        result = await create_case(body=body, current_user=user, db=db)

    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    assert "id" in result
    assert "title" in result
    assert "status" in result
    assert result["reference_number"].startswith("CASE-")


@pytest.mark.asyncio
async def test_create_case_title_propagated():
    """Case title from body is stored in the created object."""
    from routers.cases import create_case, CaseCreate

    db = _make_db()
    added = []
    db.add = MagicMock(side_effect=added.append)

    body = CaseCreate(title="Lavagem de dinheiro", severity="CRITICAL")
    user = _make_user()

    with patch("routers.cases.write_audit", AsyncMock()):
        await create_case(body=body, current_user=user, db=db)

    assert any(getattr(obj, "title", None) == "Lavagem de dinheiro" for obj in added)


# ---------------------------------------------------------------------------
# list_cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_cases_returns_items_from_repo():
    """GET /cases delegates to CaseRepository and serializes results."""
    from routers.cases import list_cases
    from repositories.cases import CaseRepository

    cases = [_make_case(f"c{i}") for i in range(3)]
    repo = MagicMock(spec=CaseRepository)
    repo.list_filtered = AsyncMock(return_value=cases)
    user = _make_user()

    result = await list_cases(
        status_filter=None, player_id=None, limit=50, offset=0,
        current_user=user, repo=repo,
    )

    assert len(result) == 3
    assert result[0]["reference_number"].startswith("CASE-")
    repo.list_filtered.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_cases_empty_repo_returns_empty_list():
    """GET /cases returns [] when no cases exist."""
    from routers.cases import list_cases
    from repositories.cases import CaseRepository

    repo = MagicMock(spec=CaseRepository)
    repo.list_filtered = AsyncMock(return_value=[])
    user = _make_user()

    result = await list_cases(
        status_filter=None, player_id=None, limit=50, offset=0,
        current_user=user, repo=repo,
    )

    assert result == []


# ---------------------------------------------------------------------------
# get_case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_case_not_found_raises_404():
    """GET /cases/{id} raises 404 when repo returns None."""
    from routers.cases import get_case
    from repositories.cases import CaseRepository
    from fastapi import HTTPException

    repo = MagicMock(spec=CaseRepository)
    repo.get_by_id = AsyncMock(return_value=None)
    db = _make_db()
    user = _make_user()

    with pytest.raises(HTTPException) as exc_info:
        await get_case(case_id="nonexistent", current_user=user, repo=repo, db=db)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_case_found_includes_required_keys():
    """GET /cases/{id} includes id, title, status, alerts, timeline."""
    from routers.cases import get_case
    from repositories.cases import CaseRepository

    case = _make_case("c1", tenant_id="t1")
    repo = MagicMock(spec=CaseRepository)
    repo.get_by_id = AsyncMock(return_value=case)

    # Mock DB for alerts and events queries
    db = _make_db()
    alerts_result = MagicMock()
    alerts_result.scalars.return_value.all.return_value = []
    events_result = MagicMock()
    events_result.scalars.return_value.all.return_value = []
    report_packages_result = MagicMock()
    report_packages_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[alerts_result, events_result, report_packages_result])

    user = _make_user(tenant_id="t1")

    result = await get_case(case_id="c1", current_user=user, repo=repo, db=db)

    for key in ("id", "title", "status", "severity", "alerts", "timeline", "report_packages"):
        assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# add_case_event — status transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_case_event_valid_transition_succeeds():
    """STATUS_CHANGE from OPEN → INVESTIGATING must succeed."""
    from routers.cases import add_case_event, CaseEventCreate

    case = _make_case("c1", tenant_id="t1", status="OPEN")
    db = _make_db()
    db.get = AsyncMock(return_value=case)

    evt_mock = MagicMock()
    evt_mock.id = "evt-1"
    evt_mock.event_type = "STATUS_CHANGE"
    evt_mock.created_at = datetime.now(UTC)
    db.refresh = AsyncMock()

    added = []
    db.add = MagicMock(side_effect=added.append)

    body = CaseEventCreate(event_type="STATUS_CHANGE", content={"new_status": "INVESTIGATING"})
    user = _make_user(tenant_id="t1")

    with patch("routers.cases.write_audit", AsyncMock()):
        result = await add_case_event(case_id="c1", body=body, current_user=user, db=db)

    assert "id" in result


@pytest.mark.asyncio
async def test_add_case_event_invalid_transition_raises_400():
    """STATUS_CHANGE from terminal REPORTED to any status must raise 400."""
    from routers.cases import add_case_event, CaseEventCreate
    from fastapi import HTTPException

    case = _make_case("c1", tenant_id="t1", status="REPORTED")
    db = _make_db()
    db.get = AsyncMock(return_value=case)

    body = CaseEventCreate(event_type="STATUS_CHANGE", content={"new_status": "OPEN"})
    user = _make_user(tenant_id="t1")

    with pytest.raises(HTTPException) as exc_info:
        with patch("routers.cases.write_audit", AsyncMock()):
            await add_case_event(case_id="c1", body=body, current_user=user, db=db)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_add_case_event_wrong_tenant_raises_404():
    """Case belonging to another tenant must raise 404."""
    from routers.cases import add_case_event, CaseEventCreate
    from fastapi import HTTPException

    case = _make_case("c1", tenant_id="t-other")
    db = _make_db()
    db.get = AsyncMock(return_value=case)

    body = CaseEventCreate(event_type="NOTE", content={"text": "Note"})
    user = _make_user(tenant_id="t1")

    with pytest.raises(HTTPException) as exc_info:
        await add_case_event(case_id="c1", body=body, current_user=user, db=db)

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# submit_report_package
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_report_package_no_rp_raises_400():
    """submit_report_package raises 400 when no ReportPackage exists."""
    from routers.cases import submit_report_package
    from fastapi import HTTPException

    case = _make_case("c1", tenant_id="t1")
    db = _make_db()
    db.get = AsyncMock(return_value=case)
    # execute for SELECT ReportPackage returns None
    rp_result = MagicMock()
    rp_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=rp_result)

    user = _make_user(tenant_id="t1")

    with patch("routers.cases.redis_rate_limit", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await submit_report_package(case_id="c1", current_user=user, db=db)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_submit_report_package_non_file_sar_raises_400():
    """submit_report_package raises 400 when decision is not FILE_SAR."""
    from routers.cases import submit_report_package
    from fastapi import HTTPException

    case = _make_case("c1", tenant_id="t1")
    rp = MagicMock()
    rp.payload = {"decision": "PENDING"}
    rp.status = "DRAFT"

    db = _make_db()
    db.get = AsyncMock(return_value=case)
    rp_result = MagicMock()
    rp_result.scalar_one_or_none.return_value = rp
    db.execute = AsyncMock(return_value=rp_result)

    user = _make_user(tenant_id="t1")

    with patch("routers.cases.redis_rate_limit", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await submit_report_package(case_id="c1", current_user=user, db=db)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_submit_report_package_already_filed_raises_409():
    """submit_report_package raises 409 when already FILED."""
    from routers.cases import submit_report_package
    from fastapi import HTTPException

    case = _make_case("c1", tenant_id="t1")
    rp = MagicMock()
    rp.payload = {"decision": "FILE_SAR"}
    rp.status = "FILED"

    db = _make_db()
    db.get = AsyncMock(return_value=case)
    rp_result = MagicMock()
    rp_result.scalar_one_or_none.return_value = rp
    db.execute = AsyncMock(return_value=rp_result)

    user = _make_user(tenant_id="t1")

    with patch("routers.cases.redis_rate_limit", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await submit_report_package(case_id="c1", current_user=user, db=db)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_report_package_sets_case_to_reported():
    """submit_report_package must synchronize case status with a filed COAF report."""
    from routers.cases import submit_report_package

    case = _make_case("c1", tenant_id="t1", status="PENDING_REVIEW")
    case.closed_at = None
    case.closed_by = None

    rp = MagicMock()
    rp.id = "rp-1"
    rp.payload = {"decision": "FILE_SAR"}
    rp.status = "FINAL"
    rp.created_by = "maker-user"

    db = _make_db()
    db.get = AsyncMock(return_value=case)
    rp_result = MagicMock()
    rp_result.scalar_one_or_none.return_value = rp
    db.execute = AsyncMock(return_value=rp_result)

    added = []
    db.add = MagicMock(side_effect=added.append)
    user = _make_user(user_id="checker-user", tenant_id="t1", role="ADMIN")

    with patch("routers.cases.redis_rate_limit", AsyncMock()), patch("routers.cases.write_audit", AsyncMock()):
        result = await submit_report_package(case_id="c1", current_user=user, db=db)

    assert result["status"] == "FILED"
    assert case.status == "REPORTED"
    assert case.closed_by == "checker-user"
    assert case.closed_at is not None
    assert rp.status == "FILED"
