"""
routers/notifications.py — Notificações do usuário autenticado.
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from libs.models import Notification
from libs.schemas import NotificationOut

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["notifications"])


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = select(Notification).where(
        _tenant_filter(Notification, current_user.tenant_id),
        Notification.user_id == current_user.id,
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa: E712
    stmt = stmt.order_by(desc(Notification.created_at)).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(
    notif_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    n = (await db.execute(
        select(Notification).where(
            Notification.id == notif_id,
            _tenant_filter(Notification, current_user.tenant_id),
            Notification.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if n is None:
        raise HTTPException(404, "Notificação não encontrada")
    n.is_read = True
    n.read_at = datetime.now(UTC)
    await db.commit()
    return {"status": "read"}


@router.post("/notifications/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    await db.execute(
        update(Notification).where(
            _tenant_filter(Notification, current_user.tenant_id),
            Notification.user_id == current_user.id,
            Notification.is_read == False,  # noqa: E712
        ).values(is_read=True, read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"status": "all_read"}
