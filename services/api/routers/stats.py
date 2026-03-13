"""
routers/stats.py — Pre-aggregated dashboard statistics.

Returns KPI counts for the current tenant in a single DB round-trip,
replacing client-side aggregation over 500-record paginated lists.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import Alert, Case, User

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard", summary="Dashboard KPI counts")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns pre-aggregated counts for the dashboard KPI cards.
    Single DB call — avoids client-side filtering of paginated lists.
    """
    tid = current_user.tenant_id
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _closed = ("CLOSED", "REPORTED", "ARCHIVED")

    # All counts in one round-trip using scalar subqueries
    alerts_today = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.created_at >= today_start,
        )
    )).scalar_one()

    critical_open = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.severity == "CRITICAL",
            Alert.status == "OPEN",
        )
    )).scalar_one()

    cases_open = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.in_(("OPEN", "IN_REVIEW", "INVESTIGATING", "PENDING_REVIEW")),
        )
    )).scalar_one()

    sla_expired = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.sla_due_at != None,  # noqa: E711
            Case.sla_due_at < now,
            Case.status.notin_(_closed),
        )
    )).scalar_one()

    auto_detected = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.auto_created == True,  # noqa: E712
        )
    )).scalar_one()

    # Per-severity open alert counts
    sev_rows = (await db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.tenant_id == tid, Alert.status == "OPEN")
        .group_by(Alert.severity)
    )).all()
    by_severity = {row[0]: row[1] for row in sev_rows}

    return {
        "alerts_today":  alerts_today,
        "critical_open": critical_open,
        "cases_open":    cases_open,
        "sla_expired":   sla_expired,
        "auto_detected": auto_detected,
        "by_severity":   by_severity,
    }
