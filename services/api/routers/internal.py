"""
routers/internal.py — Internal endpoints for infra integrations.

These routes are NOT public-facing and should be protected at the
network/ingress level (not accessible from the internet).

Currently exposes:
  POST /internal/alerts/webhook  — AlertManager webhook receiver
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from config import settings
from database import get_db
from models import Alert, Player, User
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_ALERT_SEVERITY_MAP = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "info": "INFO",
}


def _ensure_internal_e2e_enabled() -> None:
    if settings.environment not in ("development", "test"):
        raise HTTPException(status_code=404, detail="Not found")


class E2EAlertCreateIn(BaseModel):
    player_id: str | None = None
    title: str = Field(default_factory=lambda: f"E2E Alert {uuid.uuid4().hex[:8]}")
    description: str = "Alerta interno criado pela suíte E2E"
    severity: str = "HIGH"
    status: str = "OPEN"
    alert_type: str = "RULE"
    evidence: dict = Field(default_factory=dict)


@router.post(
    "/e2e/alerts",
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
    summary="Create deterministic alert fixture for E2E suites",
)
async def create_e2e_alert(
    body: E2EAlertCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST", "SUPER_ADMIN")),
) -> JSONResponse:
    _ensure_internal_e2e_enabled()

    player_id = body.player_id
    if not player_id:
        player = (
            await db.execute(
                select(Player)
                .where(Player.tenant_id == current_user.tenant_id)
                .order_by(Player.created_at.asc())
                .limit(1)
            )
        ).scalars().first()
        if player is None:
            raise HTTPException(status_code=404, detail="No player available for E2E alert fixture")
        player_id = str(player.id)

    alert = Alert(
        tenant_id=current_user.tenant_id,
        player_id=player_id,
        alert_type=body.alert_type,
        severity=body.severity,
        status=body.status,
        title=body.title,
        description=body.description,
        evidence={
            "created_via": "internal_e2e_fixture",
            "created_at": datetime.now(timezone.utc).isoformat(),
            **(body.evidence or {}),
        },
        source_event_id=f"e2e-{uuid.uuid4()}",
    )
    db.add(alert)
    await db.flush()
    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action="CREATE_E2E_ALERT",
        entity_type="Alert",
        entity_id=alert.id,
        after={"title": alert.title, "severity": alert.severity, "status": alert.status},
    )
    await db.commit()
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "id": str(alert.id),
            "player_id": str(alert.player_id) if alert.player_id else None,
            "title": alert.title,
            "severity": alert.severity,
            "status": alert.status,
            "case_id": alert.case_id,
        },
    )


@router.post(
    "/alerts/webhook",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,  # ocultar do Swagger público
    summary="AlertManager webhook receiver",
)
async def alertmanager_webhook(request: Request) -> JSONResponse:
    """
    Receives fired/resolved alert notifications from Prometheus AlertManager.

    Expected payload (AlertManager v2 API):
    {
      "version": "4",
      "groupKey": "...",
      "status": "firing" | "resolved",
      "alerts": [...]
    }

    Security: requires X-Webhook-Secret header matching INTERNAL_WEBHOOK_SECRET.
    """
    # ── Shared-secret guard (GAP-2) ──────────────────────────────────────────
    provided_secret = request.headers.get("X-Webhook-Secret", "")
    if settings.environment not in ("development", "test"):
        if provided_secret != settings.internal_webhook_secret:
            logger.warning(
                "alertmanager_webhook_unauthorized",
                remote=request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        # Em dev/test, permite secret ausente (ambiente local/CI). Se enviado,
        # ainda valida para evitar bypass acidental com valor incorreto.
        if provided_secret and provided_secret != settings.internal_webhook_secret:
            logger.warning(
                "alertmanager_webhook_unauthorized",
                remote=request.client.host if request.client else "unknown",
            )
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid JSON payload"},
        )

    group_status = body.get("status", "unknown")
    alerts = body.get("alerts", [])

    for alert in alerts:
        alert_name = alert.get("labels", {}).get("alertname", "unknown")
        severity = alert.get("labels", {}).get("severity", "unknown")
        alert_status = alert.get("status", group_status)
        summary = alert.get("annotations", {}).get("summary", "")
        description = alert.get("annotations", {}).get("description", "")

        log_level = "critical" if severity == "critical" else "warning"
        log_fn = logger.critical if log_level == "critical" else logger.warning

        log_fn(
            "prometheus_alert_received",
            alert_name=alert_name,
            severity=severity,
            status=alert_status,
            summary=summary,
            description=description,
        )

    logger.info(
        "alertmanager_webhook_processed",
        group_status=group_status,
        alert_count=len(alerts),
    )

    return JSONResponse(content={"status": "ok", "processed": len(alerts)})
