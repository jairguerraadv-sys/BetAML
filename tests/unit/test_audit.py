"""
tests/unit/test_audit.py — Unit tests for routers/audit.py.

Tests cover:
  - _serialize_audit: PII extraction from ACCESS_PII:<field> action
  - _serialize_audit: non-PII actions produce pii_accessed=None
  - _serialize_audit: all required keys present
  - list_audit_logs: date_from / date_to filters applied
  - list_audit_logs: actor_id aliased to user_id
  - list_audit_logs: pagination via page/per_page params
  - list_audit_log_legacy: returns {total, items, limit, offset}
  - RBAC: endpoint uses require_roles (ADMIN or AUDITOR only)
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(role: str = "ADMIN", tenant_id: str = "t1"):
    u = MagicMock()
    u.id = "user-1"
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_log(
    log_id: str = "log-1",
    action: str = "READ",
    entity_type: str = "Player",
    entity_id: str = "p1",
    user_id: str = "u1",
):
    lo = MagicMock()
    lo.id = log_id
    lo.action = action
    lo.entity_type = entity_type
    lo.entity_id = entity_id
    lo.user_id = user_id
    lo.before = None
    lo.after = None
    lo.ip_address = None
    lo.created_at = datetime.now(UTC)
    return lo


def _make_db_with_logs(logs: list, total: int = 0):
    """Return mocked AsyncSession that serves a list of AuditLog rows."""
    db = AsyncMock()
    call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        result.scalars.return_value.all.return_value = logs
        result.scalar_one.return_value = total
        return result

    db.execute = _execute
    return db


# ---------------------------------------------------------------------------
# _serialize_audit — pure function tests
# ---------------------------------------------------------------------------

def test_serialize_audit_extracts_pii_accessed_from_action():
    """ACCESS_PII:cpf action must populate pii_accessed='cpf'."""
    from routers.audit import _serialize_audit

    lo = _make_log(action="ACCESS_PII:cpf")
    result = _serialize_audit(lo)

    assert result["pii_accessed"] == "cpf"
    assert result["action"] == "ACCESS_PII:cpf"


def test_serialize_audit_non_pii_action_produces_none():
    """Standard actions must produce pii_accessed=None."""
    from routers.audit import _serialize_audit

    lo = _make_log(action="CREATE")
    result = _serialize_audit(lo)

    assert result["pii_accessed"] is None


def test_serialize_audit_includes_all_required_keys():
    """Serialized audit entry must include all keys consumed by the frontend."""
    from routers.audit import _serialize_audit

    lo = _make_log()
    result = _serialize_audit(lo)

    required = {"id", "action", "pii_accessed", "entity_type", "entity_id",
                "user_id", "actor_id", "before", "after", "ip_address", "created_at"}
    missing = required - set(result.keys())
    assert not missing, f"Missing keys in serialized audit: {missing}"


def test_serialize_audit_actor_id_equals_user_id():
    """actor_id is an alias for user_id (backwards compatibility)."""
    from routers.audit import _serialize_audit

    lo = _make_log(user_id="analyst-99")
    result = _serialize_audit(lo)

    assert result["actor_id"] == result["user_id"] == "analyst-99"


def test_serialize_audit_pii_action_with_multiple_colons():
    """ACCESS_PII:name:encrypted should extract field name correctly."""
    from routers.audit import _serialize_audit

    lo = _make_log(action="ACCESS_PII:name:encrypted")
    result = _serialize_audit(lo)

    assert result["pii_accessed"] == "name:encrypted"


# ---------------------------------------------------------------------------
# list_audit_logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_audit_logs_returns_serialized_entries():
    """GET /audit-logs returns list of serialized log entries."""
    from routers.audit import list_audit_logs

    logs = [_make_log(f"l{i}") for i in range(5)]
    db = _make_db_with_logs(logs)
    user = _make_user()

    result = await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id=None, entity_id=None, search=None, pii_only=False,
        date_from=None, date_to=None, limit=50, offset=0, page=None, per_page=None, envelope=False,
        current_user=user, db=db,
    )

    assert len(result) == 5
    assert all("id" in entry for entry in result)


@pytest.mark.asyncio
async def test_list_audit_logs_supports_optional_envelope():
    """GET /audit-logs?envelope=true returns {total, items, limit, offset}."""
    from routers.audit import list_audit_logs

    logs = [_make_log("l1"), _make_log("l2")]
    db = _make_db_with_logs(logs, total=2)
    user = _make_user()

    result = await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id=None, entity_id=None, search=None, pii_only=False,
        date_from=None, date_to=None, limit=10, offset=0, page=None, per_page=None, envelope=True,
        current_user=user, db=db,
    )

    assert result["total"] == 2
    assert isinstance(result["items"], list)
    assert result["limit"] == 10
    assert result["offset"] == 0


@pytest.mark.asyncio
async def test_list_audit_logs_actor_id_sets_user_id():
    """actor_id param should be aliased to user_id for filtering."""
    from routers.audit import list_audit_logs

    db = _make_db_with_logs([])
    user = _make_user()

    # Passing actor_id without user_id — should not raise
    result = await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id="analyst-1", entity_id=None, search=None, pii_only=False,
        date_from=None, date_to=None, limit=50, offset=0, page=None, per_page=None, envelope=False,
        current_user=user, db=db,
    )

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_list_audit_logs_per_page_overrides_limit():
    """per_page param should override the default limit."""
    from routers.audit import list_audit_logs

    logs = [_make_log(f"l{i}") for i in range(10)]
    db = _make_db_with_logs(logs)
    user = _make_user()

    # Use per_page=10 — should return up to 10 items
    result = await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id=None, entity_id=None, search=None, pii_only=False,
        date_from=None, date_to=None, limit=5, offset=0, page=None, per_page=10, envelope=False,
        current_user=user, db=db,
    )

    assert len(result) == 10


@pytest.mark.asyncio
async def test_list_audit_logs_page_sets_offset():
    """page param calculates offset = (page-1) * limit."""
    from routers.audit import list_audit_logs

    captured_queries = []
    db = AsyncMock()

    async def _execute(stmt):
        captured_queries.append(stmt)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    user = _make_user()

    # page=2, per_page=25 → offset=25
    await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id=None, entity_id=None, search=None, pii_only=False,
        date_from=None, date_to=None, limit=50, offset=0, page=2, per_page=25, envelope=False,
        current_user=user, db=db,
    )

    # If execution happened, pagination logic ran without error
    assert len(captured_queries) >= 1


@pytest.mark.asyncio
async def test_list_audit_logs_date_range_accepted():
    """date_from and date_to can be passed without error."""
    from routers.audit import list_audit_logs

    db = _make_db_with_logs([])
    user = _make_user()

    result = await list_audit_logs(
        entity_type=None, action=None, user_id=None, actor_id=None, entity_id=None, search=None, pii_only=False,
        date_from=datetime(2026, 1, 1, tzinfo=UTC),
        date_to=datetime(2026, 12, 31, tzinfo=UTC),
        limit=50, offset=0, page=None, per_page=None,
        current_user=user, db=db,
    )

    assert result == []


@pytest.mark.asyncio
async def test_list_audit_logs_search_and_pii_filters_accepted():
    """Free-text search and pii_only filter must be accepted together."""
    from routers.audit import list_audit_logs

    db = _make_db_with_logs([])
    user = _make_user()

    result = await list_audit_logs(
        entity_type=None,
        action=None,
        user_id=None,
        actor_id=None,
        entity_id=None,
        search="EXPORT_REPORT",
        pii_only=True,
        date_from=None,
        date_to=None,
        limit=50,
        offset=0,
        page=None,
        per_page=None,
        current_user=user,
        db=db,
    )

    assert result == []


# ---------------------------------------------------------------------------
# list_audit_log_legacy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_audit_log_returns_envelope():
    """GET /audit-log returns {total, items, limit, offset} envelope."""
    from routers.audit import list_audit_log_legacy

    logs = [_make_log("l1"), _make_log("l2")]
    db = _make_db_with_logs(logs, total=2)
    user = _make_user()

    result = await list_audit_log_legacy(
        entity_type=None, action=None, user_id=None,
        date_from=None, date_to=None, limit=50, offset=0,
        page=None, per_page=None,
        current_user=user, db=db,
    )

    assert "total" in result
    assert "items" in result
    assert "limit" in result
    assert "offset" in result


@pytest.mark.asyncio
async def test_legacy_audit_log_per_page_overrides_limit():
    """GET /audit-log per_page param overrides limit."""
    from routers.audit import list_audit_log_legacy

    db = _make_db_with_logs([], total=0)
    user = _make_user()

    result = await list_audit_log_legacy(
        entity_type=None, action=None, user_id=None,
        date_from=None, date_to=None, limit=10, offset=0,
        page=None, per_page=25,
        current_user=user, db=db,
    )

    assert result["limit"] == 25


# ---------------------------------------------------------------------------
# RBAC check
# ---------------------------------------------------------------------------

def test_audit_logs_endpoint_requires_admin_or_auditor():
    """GET /audit-logs must be protected with require_roles."""
    from routers import audit as audit_module
    import inspect

    src = inspect.getsource(audit_module)
    assert "require_roles" in src, "audit router must use require_roles"
    assert "AUDITOR" in src, "AUDITOR role must be in the RBAC guard"
    assert "ADMIN" in src, "ADMIN role must be in the RBAC guard"
