"""routers/alerts.py — Listagem, detalhe, triage, close, link-to-case, SSE stream, labeling."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Literal, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import AsyncSessionLocal, get_db
from models import Alert, Bet, Case, FinancialTransaction, Notification, User
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
        last_seen_at = datetime.now(timezone.utc)
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
    a.triaged_at = datetime.now(timezone.utc)
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

    alert_ts = a.created_at or datetime.now(timezone.utc)
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


# ── Alert Labeling (M5 — feedback loop) ──────────────────────────────────────

UTC = timezone.utc


class AlertLabelIn(BaseModel):
    label: Literal["TRUE_POSITIVE", "FALSE_POSITIVE", "NEED_REVIEW"]
    label_note: str | None = None


@router.post("/alerts/{alert_id}/label")
async def label_alert(
    alert_id: str,
    body: AlertLabelIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Label an alert as TRUE_POSITIVE, FALSE_POSITIVE, or NEED_REVIEW."""
    alert = (await db.execute(
        select(Alert).where(
            Alert.id == alert_id,
            Alert.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()
    if alert is None:
        raise HTTPException(404, "Alert not found")

    alert.label = body.label
    alert.label_note = body.label_note
    alert.labeled_by = current_user.id
    alert.labeled_at = datetime.now(UTC)
    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.id,
        action="LABEL_ALERT",
        entity_type="Alert",
        entity_id=alert_id,
        after={"label": body.label, "label_note": body.label_note},
    )
    await db.commit()
    background_tasks.add_task(_enqueue_feedback_event, alert_id, body.label, current_user.tenant_id)
    return {"status": "labeled", "label": body.label}


async def _enqueue_feedback_event(alert_id: str, label: str, tenant_id: str) -> None:
    """Publish labeled alert event to Kafka for ML retraining pipeline.

    Retries up to 2 times on transient failures; on final failure stores
    a Notification for every ADMIN user so the failure is visible in the UI.
    """
    MAX_RETRIES = 2
    last_exc: Exception | None = None

    payload = json.dumps({
        "alert_id": alert_id,
        "label": label,
        "tenant_id": tenant_id,
        "ts": datetime.now(UTC).isoformat(),
    }).encode()

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Reuse the shared Kafka producer held by main.py; lazy-import to
            # avoid circular dependency (routers are loaded after main).
            from main import get_producer  # noqa: PLC0415
            producer = await get_producer()
            if producer is None:
                raise RuntimeError("Kafka producer not available")
            await producer.send("feedback.labels", payload)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))

    logger.warning(
        "feedback_event_publish_failed",
        alert_id=alert_id,
        tenant_id=tenant_id,
        label=label,
        error=str(last_exc),
        attempts=MAX_RETRIES + 1,
    )

    try:
        async with AsyncSessionLocal() as _db:
            admin_ids = list((
                await _db.execute(
                    select(User.id).where(
                        User.tenant_id == tenant_id,
                        User.role == "ADMIN",
                        User.active == True,  # noqa: E712
                    )
                )
            ).scalars().all())
            for admin_id in admin_ids:
                _db.add(Notification(
                    tenant_id=tenant_id,
                    user_id=admin_id,
                    type="SYSTEM_ERROR",
                    title="Falha na publicação de feedback label",
                    body=(
                        f"Feedback label para alert {alert_id} falhou ao publicar "
                        f"após {MAX_RETRIES + 1} tentativas — revisão manual necessária."
                    ),
                    reference_type="alert",
                    reference_id=alert_id,
                ))
            await _db.commit()
    except Exception as db_exc:  # noqa: BLE001
        logger.error("feedback_notification_store_failed", alert_id=alert_id, error=str(db_exc))
