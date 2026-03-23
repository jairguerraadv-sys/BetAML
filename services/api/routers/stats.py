"""
routers/stats.py — Pre-aggregated dashboard statistics.

Returns KPI counts for the current tenant in a single DB round-trip,
replacing client-side aggregation over 500-record paginated lists.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import Alert, Case, IngestJob, Player, User

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
    tomorrow = today_start + timedelta(days=1)
    since_30d = today_start - timedelta(days=29)
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

    alerts_open = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.status == "OPEN",
        )
    )).scalar_one()

    cases_investigating = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.in_(("INVESTIGATING", "PENDING_REVIEW", "IN_REVIEW")),
        )
    )).scalar_one()

    cases_near_sla = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.sla_due_at != None,  # noqa: E711
            Case.sla_due_at >= now,
            Case.sla_due_at < now + timedelta(hours=24),
            Case.status.notin_(_closed),
        )
    )).scalar_one()

    high_risk_players = (await db.execute(
        select(func.count(Player.id)).where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
            Player.risk_band == "HIGH",
        )
    )).scalar_one()

    events_ingested_today = int((await db.execute(
        select(func.coalesce(func.sum(IngestJob.processed_records), 0)).where(
            IngestJob.tenant_id == tid,
            IngestJob.created_at >= today_start,
            IngestJob.created_at < tomorrow,
        )
    )).scalar_one() or 0)

    # Per-severity open alert counts
    sev_rows = (await db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.tenant_id == tid, Alert.status == "OPEN")
        .group_by(Alert.severity)
    )).all()
    by_severity = {row[0]: row[1] for row in sev_rows}

    recent_alert_rows = (await db.execute(
        select(Alert.created_at, Alert.severity, Alert.alert_type).where(
            Alert.tenant_id == tid,
            Alert.created_at >= since_30d,
        )
    )).all()

    timeline_map = {
        (today_start - timedelta(days=offset)).date().isoformat(): {
            "date": (today_start - timedelta(days=offset)).date().isoformat(),
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "total": 0,
        }
        for offset in range(29, -1, -1)
    }
    rule_type_counter: Counter[str] = Counter()
    heatmap_map: dict[tuple[int, int], int] = {}

    for created_at, severity, alert_type in recent_alert_rows:
        if not created_at:
            continue
        key = created_at.date().isoformat()
        if key in timeline_map:
            bucket = timeline_map[key]
            sev = str(severity or "").upper()
            if sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                bucket[sev] += 1
            bucket["total"] += 1
        rule_type_counter[str(alert_type or "UNKNOWN")] += 1
        weekday = int(created_at.weekday())
        hour = int(created_at.hour)
        heatmap_map[(weekday, hour)] = heatmap_map.get((weekday, hour), 0) + 1

    top_players_rows = (await db.execute(
        select(Player.id, Player.external_player_id, Player.risk_score, Player.risk_band).where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
        )
        .order_by(Player.risk_score.desc(), Player.updated_at.desc())
        .limit(10)
    )).all()

    return {
        "generated_at": now,
        "alerts_today":  alerts_today,
        "critical_open": critical_open,
        "cases_open":    cases_open,
        "sla_expired":   sla_expired,
        "auto_detected": auto_detected,
        "by_severity":   by_severity,
        "alerts_open": alerts_open,
        "cases_investigating": cases_investigating,
        "cases_near_sla": cases_near_sla,
        "high_risk_players": high_risk_players,
        "events_ingested_today": events_ingested_today,
        "alerts_by_severity_30d": list(timeline_map.values()),
        "alerts_by_rule_type": [
            {"label": label, "value": count}
            for label, count in rule_type_counter.most_common(10)
        ],
        "top_players_by_risk": [
            {
                "player_id": str(row[0]),
                "external_player_id": row[1],
                "risk_score": float(row[2] or 0),
                "risk_band": row[3] or "UNKNOWN",
            }
            for row in top_players_rows
        ],
        "alert_heatmap": [
            {"weekday": weekday, "hour": hour, "count": count}
            for (weekday, hour), count in sorted(heatmap_map.items())
        ],
    }
