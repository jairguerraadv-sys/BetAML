"""
routers/notifications.py — Notificações do usuário autenticado.
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from libs.models import Notification
from libs.schemas import NotificationOut

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["notifications"])


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    envelope: bool = Query(False, description="Quando true, retorna {items,total,limit,offset}."),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    envelope_enabled = envelope if isinstance(envelope, bool) else False
    filters = [
        _tenant_filter(Notification, current_user.tenant_id),
        Notification.user_id == current_user.id,
    ]
    if unread_only:
        filters.append(Notification.is_read == False)  # noqa: E712

    stmt = (
        select(Notification)
        .where(*filters)
        .order_by(desc(Notification.created_at))
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    items = result.scalars().all()
    if not envelope_enabled:
        return items

    total_q = select(func.count()).select_from(Notification).where(*filters)
    total = (await db.execute(total_q)).scalar_one()
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


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
