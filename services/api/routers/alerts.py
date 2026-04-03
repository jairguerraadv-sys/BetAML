"""routers/alerts.py — Listagem, detalhe, triage, close, link-to-case, SSE stream, labeling."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Literal, Optional

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, get_current_user, get_effective_roles, require_roles, require_role, require_role_any, require_permission
from database import AsyncSessionLocal, get_db
from libs.schemas import AlertExplainabilityOut
from models import Alert, Bet, Case, FinancialTransaction, Notification, User
from repositories import AlertRepository
from repositories.alerts import get_alert_repo
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["alerts"])


def _numeric_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _infer_feature_baseline(feature_name: str, snapshot: dict[str, object]) -> float | None:
    direct_candidates = [
        f"baseline_{feature_name}",
        f"{feature_name}_baseline",
    ]
    if feature_name.startswith("deposit_"):
        direct_candidates.extend([
            "baseline_avg_daily_deposit",
            "baseline_avg_deposit",
            "baseline_deposit_avg_30d",
        ])
    if feature_name.endswith("_ratio") or "ratio" in feature_name:
        direct_candidates.append("baseline_ratio")
    for key in direct_candidates:
        baseline = _numeric_or_none(snapshot.get(key))
        if baseline is not None:
            return baseline
    if feature_name.startswith("shared_") or feature_name.endswith("_count") or feature_name.endswith("_flag"):
        return 0.0
    if feature_name.endswith("_ratio") or "ratio" in feature_name or feature_name.startswith("zscore_"):
        return 0.0
    return None


def _build_ml_explainability(alert: Alert) -> dict[str, object] | None:
    evidence = alert.evidence or {}
    if not isinstance(evidence, dict):
        return None

    feature_snapshot = evidence.get("feature_snapshot") or {}
    if not isinstance(feature_snapshot, dict):
        feature_snapshot = {}

    top_drivers = evidence.get("top_drivers") or []
    if not isinstance(top_drivers, list):
        top_drivers = []

    shap_values = evidence.get("shap_values") or {}
    if not isinstance(shap_values, dict):
        shap_values = {}

    if alert.anomaly_score is None and not top_drivers and not feature_snapshot:
        return None

    ranked_features: list[str] = []
    for feature_name in top_drivers:
        feature = str(feature_name)
        if feature and feature not in ranked_features:
            ranked_features.append(feature)

    if not ranked_features:
        numeric_candidates = [
            (name, abs(_numeric_or_none(value) or 0.0))
            for name, value in feature_snapshot.items()
            if _numeric_or_none(value) is not None
        ]
        ranked_features = [name for name, _ in sorted(numeric_candidates, key=lambda item: item[1], reverse=True)[:5]]

    features_payload = []
    for index, feature_name in enumerate(ranked_features[:5]):
        current_value = feature_snapshot.get(feature_name)
        current_numeric = _numeric_or_none(current_value)
        baseline_value = _infer_feature_baseline(feature_name, feature_snapshot)
        delta = None
        if current_numeric is not None and baseline_value is not None:
            delta = round(current_numeric - baseline_value, 4)

        shap_contribution = _numeric_or_none(shap_values.get(feature_name))
        if shap_contribution is not None:
            contribution = shap_contribution
        elif delta is not None:
            contribution = delta
        elif current_numeric is not None:
            contribution = current_numeric
        else:
            contribution = max(0.1, 1.0 - (index * 0.15))

        features_payload.append({
            "feature": feature_name,
            "current_value": current_value,
            "baseline_value": baseline_value,
            "delta": delta,
            "contribution": round(float(contribution), 4),
        })

    features_payload.sort(key=lambda item: abs(float(item["contribution"])), reverse=True)
    return {
        "alert_id": str(alert.id),
        "model_id": str(evidence.get("model_id")) if evidence.get("model_id") else None,
        "explanation_method": "shap" if shap_values else "heuristic_proxy",
        "anomaly_score": float(alert.anomaly_score or 0.0),
        "top_features": features_payload[:5],
    }


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
    repo: AlertRepository = Depends(get_alert_repo),
):
    # Support both limit/offset and page/per_page pagination styles
    if per_page is not None:
        limit = per_page
    if page is not None:
        offset = (page - 1) * limit

    alerts = await repo.list_filtered(
        current_user.tenant_id,
        severity=severity,
        status=status_filter,
        player_id=player_id,
        rule_id=rule_id,
        limit=limit,
        offset=offset,
    )
    total = await repo.count_filtered(current_user.tenant_id)

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
    repo: AlertRepository = Depends(get_alert_repo),
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
            for a in await repo.list_open_recent(tenant_id, limit=10):
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
                new_alerts = await repo.list_open_recent(
                    tenant_id, limit=50, created_after=last_seen_at
                )
                if new_alerts:
                    last_seen_at = new_alerts[-1].created_at or last_seen_at
                    for a in sorted(new_alerts, key=lambda x: x.created_at or last_seen_at):
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
        "composite_score": float(a.composite_score) if a.composite_score else None,
        "score_breakdown": a.score_breakdown or {},
        "source_event_id": a.source_event_id,
        "case_id": a.case_id, "created_at": a.created_at,
        "triaged_by": a.triaged_by, "triaged_at": a.triaged_at,
        "label": a.label, "label_note": a.label_note, "labeled_at": a.labeled_at,
    }


@router.get("/alerts/{alert_id}/explainability", response_model=AlertExplainabilityOut)
async def get_alert_explainability(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    alert = await db.get(Alert, alert_id)
    if not alert or alert.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    explainability = _build_ml_explainability(alert)
    if explainability is None:
        raise HTTPException(404, "Explicabilidade indisponível para este alerta")
    return explainability


class TriageRequest(BaseModel):
    disposition: Literal["IN_REVIEW", "CONFIRMED", "DISMISSED", "FALSE_POSITIVE"] = "IN_REVIEW"
    note: Optional[str] = None
    notes: Optional[str] = None  # legacy alias


@router.post("/alerts/{alert_id}/triage")
async def triage_alert(
    alert_id: str,
    body: TriageRequest,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
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
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
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
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
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
    product_type: str | None = Query(None, description="Filtrar apostas por modalidade (SPORTSBOOK, CASINO_LIVE, SLOT, ...)"),
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
            *([Bet.product_type == product_type] if product_type else []),
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
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR, AppRole.SUPER_ADMIN])),
):
    """Label an alert as TRUE_POSITIVE, FALSE_POSITIVE, or NEED_REVIEW."""
    if not get_effective_roles(current_user).intersection({AppRole.ANALISTA, AppRole.GESTOR, AppRole.SUPER_ADMIN}):
        raise HTTPException(403, "Forbidden")
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
        user_id=current_user.id,
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

    payload = {
        "alert_id": alert_id,
        "label": label,
        "tenant_id": tenant_id,
        "ts": datetime.now(UTC).isoformat(),
    }

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
                        User.role.in_(["ADMIN", "AML_ANALYST", AppRole.GESTOR, AppRole.ANALISTA]),
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
