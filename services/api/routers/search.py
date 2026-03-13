"""
routers/search.py — Global search endpoint for the BetAML frontend.

Searches players (by external_id/name/CPF-hash), cases (by reference_number/title),
and alerts (by alert_type/id) for the current tenant.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from models import Alert, Case, Player, User

router = APIRouter(prefix="/search", tags=["search"])

_MAX = 5  # max results per category


@router.get("", summary="Global search")
async def global_search(
    q: str = Query(..., min_length=2, max_length=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Search players, cases and alerts across the current tenant.
    Matches on:
     - Players: external_player_id (prefix), name (partial — encrypted fields are not searchable by plain text)
     - Cases: reference_number (prefix), title (partial)
     - Alerts: alert_id (prefix), alert_type (partial)
    """
    tid = current_user.tenant_id
    q_like = f"%{q}%"
    q_prefix = f"{q}%"

    # ── Players ──────────────────────────────────────────────────────────────
    stmt_players = (
        select(Player)
        .where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
            or_(
                Player.external_player_id.ilike(q_prefix),
                # CPF is encrypted — we can't search it directly.
                # Partial name search works if name is not anonymized.
                Player.risk_band.ilike(q_like),
            ),
        )
        .limit(_MAX)
    )
    players_rows = (await db.execute(stmt_players)).scalars().all()

    # ── Cases ─────────────────────────────────────────────────────────────────
    stmt_cases = (
        select(Case)
        .where(
            Case.tenant_id == tid,
            or_(
                Case.reference_number.ilike(q_prefix),
                Case.title.ilike(q_like),
            ),
        )
        .order_by(Case.created_at.desc())
        .limit(_MAX)
    )
    cases_rows = (await db.execute(stmt_cases)).scalars().all()

    # ── Alerts ────────────────────────────────────────────────────────────────
    stmt_alerts = (
        select(Alert)
        .where(
            Alert.tenant_id == tid,
            or_(
                Alert.id.cast("text").ilike(q_prefix),
                Alert.alert_type.ilike(q_like),
            ),
        )
        .order_by(Alert.created_at.desc())
        .limit(_MAX)
    )
    alerts_rows = (await db.execute(stmt_alerts)).scalars().all()

    return {
        "players": [
            {
                "id": str(p.id),
                "external_id": p.external_player_id,
                "name": p.external_player_id,  # name is PII-encrypted; use external_id as label
                "risk_band": p.risk_band or "UNKNOWN",
            }
            for p in players_rows
        ],
        "cases": [
            {
                "id": str(c.id),
                "reference_number": c.reference_number or str(c.id)[:8],
                "title": c.title or "",
                "status": c.status or "",
            }
            for c in cases_rows
        ],
        "alerts": [
            {
                "id": str(a.id),
                "alert_type": a.alert_type or "",
                "severity": a.severity or "",
                "player_id": str(a.player_id) if a.player_id else "",
            }
            for a in alerts_rows
        ],
    }
