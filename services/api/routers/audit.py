"""routers/audit.py — Listagem de AuditLog (ADMIN/AUDITOR only)."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from database import get_db
from models import AuditLog, User

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["audit"])


@router.get("/audit-logs")
async def list_audit_logs(
    entity_type: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: User = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    q = select(AuditLog).where(AuditLog.tenant_id == current_user.tenant_id)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    q = q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    logs = (await db.execute(q)).scalars().all()
    return [
        {
            "id": lo.id, "action": lo.action, "entity_type": lo.entity_type,
            "entity_id": lo.entity_id, "user_id": lo.user_id,
            "before": lo.before, "after": lo.after, "created_at": lo.created_at,
        }
        for lo in logs
    ]
