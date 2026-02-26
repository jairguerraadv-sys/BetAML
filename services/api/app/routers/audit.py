from typing import Annotated, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, require_auditor_or_admin
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    current: Annotated[CurrentUser, Depends(require_auditor_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    size: int = 20,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
) -> AuditLogListResponse:
    q = select(AuditLog).where(AuditLog.tenant_id == current.tenant_id)
    if action:
        q = q.where(AuditLog.action == action)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    q = q.order_by(AuditLog.created_at.desc())

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()
    offset = (page - 1) * size
    result = await db.execute(q.offset(offset).limit(size))
    logs = result.scalars().all()
    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(log) for log in logs],
        total=total,
        page=page,
        size=size,
    )
