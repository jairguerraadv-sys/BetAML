"""
routers/internal.py — Internal endpoints for infra integrations.

These routes are NOT public-facing and should be protected at the
network/ingress level (not accessible from the internet).

Currently exposes:
  POST /internal/alerts/webhook  — AlertManager webhook receiver
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

_ALERT_SEVERITY_MAP = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "info": "INFO",
}


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
    if provided_secret != settings.internal_webhook_secret:
        logger.warning("alertmanager_webhook_unauthorized", remote=request.client.host if request.client else "unknown")
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
