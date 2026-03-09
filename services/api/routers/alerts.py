"""routers/alerts.py — Listagem, detalhe, triage, close, link-to-case, SSE stream."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import AsyncGenerator, Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from models import Alert, Bet, Case, FinancialTransaction, User
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def list_alerts(
    severity: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    player_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    page: Optional[int] = Query(None, ge=1),
    per_page: Optional[int] = Query(None, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Support both limit/offset and page/per_page pagination styles
    if per_page is not None:
        limit = per_page
    if page is not None:
        offset = (page - 1) * limit
    q = select(Alert).where(Alert.tenant_id == current_user.tenant_id)
    if severity:      q = q.where(Alert.severity == severity)
    if status_filter: q = q.where(Alert.status == status_filter)
    if player_id:     q = q.where(Alert.player_id == player_id)
    if rule_id:       q = q.where(Alert.rule_id == rule_id)
    q = q.order_by(Alert.created_at.desc()).limit(limit).offset(offset)

    count_q = select(sqlfunc.count()).select_from(Alert).where(Alert.tenant_id == current_user.tenant_id)
    total = (await db.execute(count_q)).scalar()

    result = await db.execute(q)
    alerts = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": a.id, "severity": a.severity, "status": a.status,
                "title": a.title, "alert_type": a.alert_type,
                "player_id": a.player_id, "rule_id": a.rule_id,
                "anomaly_score": float(a.anomaly_score) if a.anomaly_score else None,
                "case_id": a.case_id, "created_at": a.created_at,
            }
            for a in alerts
        ],
    }


@router.get("/alerts/stream")
async def stream_alerts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events (SSE): envia novos alertas OPEN em tempo real.
    O cliente connect com EventSource('/api-proxy/alerts/stream').
    Polling interno a cada 5s — sem necessidade de WebSocket.
    """
    tenant_id = current_user.tenant_id

    async def event_generator() -> AsyncGenerator[str, None]:
        # Envia os 10 alertas mais recentes imediatamente ao conectar
        last_seen_at = datetime.utcnow()
        try:
            q = (
                select(Alert)
                .where(Alert.tenant_id == tenant_id, Alert.status == "OPEN")
                .order_by(Alert.created_at.desc())
                .limit(10)
            )
            result = await db.execute(q)
            for a in result.scalars().all():
                data = json.dumps({
                    "id": a.id, "severity": a.severity, "title": a.title,
                    "status": a.status, "created_at": a.created_at.isoformat() if a.created_at else None,
                })
                yield f"event: alert\ndata: {data}\n\n"
        except Exception as exc:
            logger.warning("sse_initial_fetch_error", error=str(exc))

        # Loop de long-polling: verifica novos alertas a cada 5s
        while True:
            try:
                await asyncio.sleep(5)
                new_q = (
                    select(Alert)
                    .where(
                        Alert.tenant_id == tenant_id,
                        Alert.status == "OPEN",
                        Alert.created_at > last_seen_at,
                    )
                    .order_by(Alert.created_at.asc())
                )
                result = await db.execute(new_q)
                new_alerts = result.scalars().all()
                if new_alerts:
                    last_seen_at = new_alerts[-1].created_at or last_seen_at
                    for a in new_alerts:
                        data = json.dumps({
                            "id": a.id, "severity": a.severity, "title": a.title,
                            "status": a.status,
                            "created_at": a.created_at.isoformat() if a.created_at else None,
                        })
                        yield f"event: alert\ndata: {data}\n\n"
                else:
                    # Heartbeat a cada ciclo para evitar timeout de proxy
                    yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("sse_poll_error", error=str(exc))
                yield ": error\n\n"
                await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/alerts/{alert_id}")
async def get_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    return {
        "id": a.id, "severity": a.severity, "status": a.status,
        "title": a.title, "description": a.description,
        "alert_type": a.alert_type, "evidence": a.evidence,
        "player_id": a.player_id, "rule_id": a.rule_id,
        "anomaly_score": float(a.anomaly_score) if a.anomaly_score else None,
        "source_event_id": a.source_event_id,
        "case_id": a.case_id, "created_at": a.created_at,
    }


class TriageRequest(BaseModel):
    disposition: Literal["IN_REVIEW", "CONFIRMED", "DISMISSED", "FALSE_POSITIVE"] = "IN_REVIEW"
    note: Optional[str] = None
    notes: Optional[str] = None  # legacy alias


@router.post("/alerts/{alert_id}/triage")
async def triage_alert(
    alert_id: str,
    body: TriageRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    a.status = body.disposition
    a.triaged_by = current_user.id
    a.triaged_at = datetime.utcnow()
    await write_audit(db, current_user.tenant_id, current_user.id, "TRIAGE", "Alert", alert_id)
    await db.commit()
    return {"id": alert_id, "status": a.status}


@router.post("/alerts/{alert_id}/close")
async def close_alert(
    alert_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    a.status = "CLOSED"
    await write_audit(db, current_user.tenant_id, current_user.id, "CLOSE", "Alert", alert_id)
    await db.commit()
    return {"id": alert_id, "status": "CLOSED"}


class LinkCaseRequest(BaseModel):
    case_id: str


@router.post("/alerts/{alert_id}/link-to-case")
async def link_alert_to_case(
    alert_id: str,
    body: LinkCaseRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    c = await db.get(Case, body.case_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    a.case_id = body.case_id
    await write_audit(db, current_user.tenant_id, current_user.id, "LINK_CASE", "Alert", alert_id, after={"case_id": body.case_id})
    await db.commit()
    return {"alert_id": alert_id, "case_id": body.case_id}


@router.get("/alerts/{alert_id}/related-transactions")
async def get_alert_related_transactions(
    alert_id: str,
    window_hours: int = Query(48, ge=1, le=720),
    limit: int = Query(50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    if not a.player_id:
        return {"transactions": [], "bets": [], "alert_id": alert_id}

    alert_ts = a.created_at or datetime.utcnow()
    if alert_ts.tzinfo is not None:
        alert_ts = alert_ts.replace(tzinfo=None)
    window_delta = timedelta(hours=window_hours)
    ts_from = alert_ts - window_delta
    ts_to   = alert_ts + window_delta

    txns = (await db.execute(
        select(FinancialTransaction)
        .where(
            FinancialTransaction.tenant_id == current_user.tenant_id,
            FinancialTransaction.player_id == a.player_id,
            FinancialTransaction.occurred_at >= ts_from,
            FinancialTransaction.occurred_at <= ts_to,
        )
        .order_by(FinancialTransaction.occurred_at.desc())
        .limit(limit)
    )).scalars().all()

    bets = (await db.execute(
        select(Bet)
        .where(
            Bet.tenant_id == current_user.tenant_id,
            Bet.player_id == a.player_id,
            Bet.occurred_at >= ts_from,
            Bet.occurred_at <= ts_to,
        )
        .order_by(Bet.occurred_at.desc())
        .limit(limit)
    )).scalars().all()

    return {
        "alert_id":   alert_id,
        "player_id":  a.player_id,
        "window_hours": window_hours,
        "transactions": [
            {
                "id": t.id, "type": t.type, "amount": float(t.amount),
                "currency": t.currency, "status": t.status,
                "payment_method": t.payment_method, "occurred_at": t.occurred_at,
            }
            for t in txns
        ],
        "bets": [
            {
                "id": b.id, "bet_type": b.bet_type, "stake_amount": float(b.stake_amount),
                "actual_payout": float(b.actual_payout) if b.actual_payout else None,
                "status": b.status, "event_name": b.event_name, "occurred_at": b.occurred_at,
            }
            for b in bets
        ],
    }
