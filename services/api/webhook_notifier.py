"""
Webhook delivery helper for BetAML.
Fire-and-forget HTTPS POST to operator-configured webhook_url.
Triggered for HIGH/CRITICAL alerts when triage is CONFIRMED.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 8.0


async def fire_webhook(webhook_url: str, payload: dict[str, Any]) -> None:
    """Send webhook POST. Fire-and-forget — never raises."""
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
            resp = await client.post(
                webhook_url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json", "User-Agent": "BetAML/1.0"},
            )
            if resp.status_code >= 400:
                logger.warning(
                    "webhook_delivery_failed url=%s status=%s",
                    webhook_url,
                    resp.status_code,
                )
    except Exception as exc:
        logger.warning("webhook_delivery_error url=%s error=%s", webhook_url, exc)


async def notify_operator_webhook(
    db,
    tenant_id: str,
    alert_id: str,
    severity: str,
    event_type: str = "alert.confirmed",
    extra: dict[str, Any] | None = None,
) -> None:
    """Look up tenant webhook_url and schedule delivery for HIGH/CRITICAL."""
    if severity not in ("HIGH", "CRITICAL"):
        return
    try:
        from sqlalchemy import text
        result = await db.execute(
            text("SELECT webhook_url FROM tenants WHERE id = :tid LIMIT 1"),
            {"tid": tenant_id},
        )
        row = result.fetchone()
        if not row or not row[0]:
            return
        payload: dict[str, Any] = {
            "event": event_type,
            "alert_id": alert_id,
            "tenant_id": tenant_id,
            "severity": severity,
            **(extra or {}),
        }
        asyncio.create_task(fire_webhook(row[0], payload))
    except Exception as exc:
        logger.warning("webhook_lookup_failed tenant_id=%s error=%s", tenant_id, exc)
