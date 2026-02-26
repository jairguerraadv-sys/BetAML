import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, require_analyst_or_admin, require_any_role
from app.models.alert import Alert, AlertStatus
from app.models.audit import AuditLog
from app.schemas.alerts import (
    AlertListResponse,
    AlertResponse,
    CloseAlertRequest,
    LinkToCaseRequest,
    TriageRequest,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    size: int = 20,
    severity: Optional[str] = None,
    alert_status: Optional[str] = None,
    player_id: Optional[str] = None,
    rule_id: Optional[uuid.UUID] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> AlertListResponse:
    q = select(Alert).where(Alert.tenant_id == current.tenant_id)
    if severity:
        q = q.where(Alert.severity == severity)
    if alert_status:
        q = q.where(Alert.status == alert_status)
    if player_id:
        q = q.where(Alert.player_id == player_id)
    if rule_id:
        q = q.where(Alert.rule_id == rule_id)
    if from_date:
        q = q.where(Alert.created_at >= from_date)
    if to_date:
        q = q.where(Alert.created_at <= to_date)
    q = q.order_by(Alert.created_at.desc())

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()
    offset = (page - 1) * size
    result = await db.execute(q.offset(offset).limit(size))
    alerts = result.scalars().all()
    return AlertListResponse(
        items=[AlertResponse.model_validate(a) for a in alerts], total=total, page=page, size=size
    )


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(
    alert_id: uuid.UUID,
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertResponse:
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == current.tenant_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return AlertResponse.model_validate(alert)


@router.post("/{alert_id}/triage", response_model=AlertResponse)
async def triage_alert(
    alert_id: uuid.UUID,
    body: TriageRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertResponse:
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == current.tenant_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    old_status = alert.status.value
    alert.status = AlertStatus.TRIAGED
    evidence = dict(alert.evidence or {})
    evidence.setdefault("triage_notes", [])
    evidence["triage_notes"].append({"note": body.note, "by": str(current.user.id)})
    alert.evidence = evidence

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="ALERT_TRIAGED",
        entity_type="Alert",
        entity_id=str(alert.id),
        old_values={"status": old_status},
        new_values={"status": AlertStatus.TRIAGED.value},
    )
    db.add(audit)
    await db.flush()
    await db.refresh(alert)
    return AlertResponse.model_validate(alert)


@router.post("/{alert_id}/close", response_model=AlertResponse)
async def close_alert(
    alert_id: uuid.UUID,
    body: CloseAlertRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertResponse:
    if body.status not in (AlertStatus.CLOSED_TP, AlertStatus.CLOSED_FP):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be CLOSED_TP or CLOSED_FP",
        )

    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == current.tenant_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    old_status = alert.status.value
    alert.status = body.status
    evidence = dict(alert.evidence or {})
    evidence["close_note"] = {"note": body.note, "by": str(current.user.id)}
    alert.evidence = evidence

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="ALERT_CLOSED",
        entity_type="Alert",
        entity_id=str(alert.id),
        old_values={"status": old_status},
        new_values={"status": body.status.value},
    )
    db.add(audit)
    await db.flush()
    await db.refresh(alert)
    return AlertResponse.model_validate(alert)


@router.post("/{alert_id}/link-to-case", response_model=AlertResponse)
async def link_alert_to_case(
    alert_id: uuid.UUID,
    body: LinkToCaseRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertResponse:
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == current.tenant_id)
    )
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    alert.case_id = body.case_id
    await db.flush()
    await db.refresh(alert)
    return AlertResponse.model_validate(alert)
