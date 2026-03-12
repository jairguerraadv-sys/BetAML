"""routers/audit.py — Listagem de AuditLog (ADMIN/AUDITOR only)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from database import get_db
from models import AuditLog, User

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["audit"])


def _serialize_audit(lo: AuditLog) -> dict:
    action = lo.action or ""
    pii_accessed: Optional[str] = None
    if action.startswith("ACCESS_PII:"):
        pii_accessed = action.split(":", 1)[1]
    return {
        "id": lo.id,
        "action": action,
        "pii_accessed": pii_accessed,
        "entity_type": lo.entity_type,
        "entity_id": lo.entity_id,
        "user_id": lo.user_id,
        "actor_id": lo.user_id,
        "before": lo.before,
        "after": lo.after,
        "ip_address": lo.ip_address,
        "created_at": lo.created_at,
    }


@router.get("/audit-logs")
async def list_audit_logs(
    entity_type: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
    actor_id: str | None = None,
    date_from: Optional[datetime] = Query(None, description="Filter from this UTC datetime (inclusive)"),
    date_to: Optional[datetime] = Query(None, description="Filter to this UTC datetime (inclusive)"),
    limit: int = Query(50, le=500),
    offset: int = 0,
    page: int | None = Query(None, ge=1),
    per_page: int | None = Query(None, ge=1, le=500),
    current_user: User = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    if per_page is not None:
        limit = per_page
    if page is not None:
        offset = (page - 1) * limit
    if actor_id and not user_id:
        user_id = actor_id

    q = select(AuditLog).where(AuditLog.tenant_id == current_user.tenant_id)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if action:
        q = q.where(AuditLog.action == action)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if date_from:
        q = q.where(AuditLog.created_at >= date_from)
    if date_to:
        q = q.where(AuditLog.created_at <= date_to)
    q = q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    logs = (await db.execute(q)).scalars().all()
    return [_serialize_audit(lo) for lo in logs]


@router.get("/audit-log")
async def list_audit_log_legacy(
    entity_type: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    page: int | None = Query(None, ge=1),
    per_page: int | None = Query(None, ge=1, le=500),
    current_user: User = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    # Legacy compatibility endpoint expected by older integrations/tests.
    if per_page is not None:
        limit = per_page
    if page is not None:
        offset = (page - 1) * limit

    filters = [AuditLog.tenant_id == current_user.tenant_id]
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if action:
        filters.append(AuditLog.action == action)
    if user_id:
        filters.append(AuditLog.user_id == user_id)
    if date_from:
        filters.append(AuditLog.created_at >= date_from)
    if date_to:
        filters.append(AuditLog.created_at <= date_to)

    total_q = select(func.count()).select_from(AuditLog).where(*filters)
    total = (await db.execute(total_q)).scalar_one()

    data_q = (
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = (await db.execute(data_q)).scalars().all()
    return {
        "total": total,
        "items": [_serialize_audit(lo) for lo in items],
        "limit": limit,
        "offset": offset,
    }
