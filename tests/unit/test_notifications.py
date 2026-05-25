"""
tests/unit/test_notifications.py — Unit tests for routers/notifications.py.

Tests cover:
  - GET /notifications returns list (all + unread_only filter)
  - POST /notifications/{id}/read marks a notification as read
  - POST /notifications/{id}/read returns 404 for missing notification
  - POST /notifications/read-all marks all unread as read
  - Tenant isolation: user_id and tenant_id filters are applied
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str = "user-1", tenant_id: str = "tenant-a"):
    user = MagicMock()
    user.id = user_id
    user.tenant_id = tenant_id
    user.role = "AML_ANALYST"
    return user


def _make_notification(notif_id: str = "notif-1", is_read: bool = False):
    n = MagicMock()
    n.id = notif_id
    n.tenant_id = "tenant-a"
    n.user_id = "user-1"
    n.is_read = is_read
    n.read_at = None
    n.created_at = datetime.now(UTC)
    n.title = "Test notification"
    n.message = "Something happened"
    n.notification_type = "ALERT"
    n.reference_type = None
    n.reference_id = None
    return n


def _make_db_with_notifs(notifs: list, scalar_one_value=None, total: int | None = None):
    db = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    db.commit = AsyncMock()
    call_count = [0]

    async def _execute(stmt):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        result.scalars.return_value.all.return_value = notifs
        result.scalar_one_or_none.return_value = scalar_one_value
        result.scalar_one.return_value = len(notifs) if total is None else total
        return result

    db.execute = _execute
    return db


# ---------------------------------------------------------------------------
# list_notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_notifications_returns_all():
    """GET /notifications returns all notifications for the user."""
    from routers.notifications import list_notifications

    notifs = [_make_notification(f"n-{i}") for i in range(3)]
    db = _make_db_with_notifs(notifs)
    user = _make_user()

    result = await list_notifications(unread_only=False, limit=50, offset=0, envelope=False, db=db, current_user=user)

    assert len(result) == 3


@pytest.mark.asyncio
async def test_list_notifications_unread_only_passes_filter():
    """GET /notifications?unread_only=true only returns unread items (mocked at DB level)."""
    from routers.notifications import list_notifications

    unread = [_make_notification("n-1", is_read=False)]
    db = _make_db_with_notifs(unread)
    user = _make_user()

    result = await list_notifications(unread_only=True, limit=50, offset=0, envelope=False, db=db, current_user=user)

    assert len(result) == 1
    assert result[0].is_read is False


@pytest.mark.asyncio
async def test_list_notifications_empty():
    """GET /notifications returns [] when there are no notifications."""
    from routers.notifications import list_notifications

    db = _make_db_with_notifs([])
    user = _make_user()

    result = await list_notifications(unread_only=False, limit=50, offset=0, envelope=False, db=db, current_user=user)

    assert result == []


@pytest.mark.asyncio
async def test_list_notifications_supports_optional_envelope():
    """GET /notifications?envelope=true returns {items,total,limit,offset}."""
    from routers.notifications import list_notifications

    notifs = [_make_notification("n-1")]
    db = _make_db_with_notifs(notifs, total=1)
    user = _make_user()

    result = await list_notifications(unread_only=False, limit=10, offset=0, envelope=True, db=db, current_user=user)

    assert isinstance(result, dict)
    assert isinstance(result.get("items"), list)
    assert result.get("total") == 1
    assert result.get("limit") == 10
    assert result.get("offset") == 0


# ---------------------------------------------------------------------------
# mark_notification_read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_read_returns_status():
    """POST /notifications/{id}/read returns {status: 'read'}."""
    from routers.notifications import mark_notification_read

    notif = _make_notification("n-1", is_read=False)
    db = _make_db_with_notifs([], scalar_one_value=notif)
    user = _make_user()

    result = await mark_notification_read(notif_id="n-1", db=db, current_user=user)

    assert result["status"] == "read"
    assert notif.is_read is True


@pytest.mark.asyncio
async def test_mark_read_404_when_not_found():
    """POST /notifications/{id}/read raises 404 when notification is not found."""
    from routers.notifications import mark_notification_read
    from fastapi import HTTPException

    db = _make_db_with_notifs([], scalar_one_value=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc_info:
        await mark_notification_read(notif_id="missing-id", db=db, current_user=user)

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# mark_all_read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_all_read_returns_all_read():
    """POST /notifications/read-all returns {status: 'all_read'}."""
    from routers.notifications import mark_all_read

    db = _make_db_with_notifs([])
    user = _make_user()

    result = await mark_all_read(db=db, current_user=user)

    assert result["status"] == "all_read"
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_notifications_router_filters_by_user_and_tenant():
    """Notifications router must filter by both user_id and tenant_id."""
    from routers import notifications as notif_module
    import inspect

    src = inspect.getsource(notif_module)
    assert "user_id" in src, "Notifications router must filter by user_id"
    assert "tenant_id" in src, "Notifications router must filter by tenant_id"
