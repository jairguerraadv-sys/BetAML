import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, require_analyst_or_admin, require_any_role
from app.models.audit import AuditLog
from app.models.case import Case, CaseEvent, CaseEventType, CaseStatus, Evidence, ReportPackage
from app.schemas.cases import (
    CaseAssignRequest,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseEventCreateRequest,
    CaseEventResponse,
    CaseListResponse,
    CaseResponse,
    CaseUpdateRequest,
    EvidenceResponse,
    ReportPackageRequest,
    ReportPackageResponse,
)

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _S3_AVAILABLE = True
except ImportError:
    _S3_AVAILABLE = False

router = APIRouter(prefix="/cases", tags=["cases"])


async def _get_case_or_404(db: AsyncSession, case_id: uuid.UUID, tenant_id: uuid.UUID) -> Case:
    result = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.get("", response_model=CaseListResponse)
async def list_cases(
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    size: int = 20,
    case_status: Optional[CaseStatus] = None,
    player_id: Optional[str] = None,
    assigned_to: Optional[uuid.UUID] = None,
) -> CaseListResponse:
    q = select(Case).where(Case.tenant_id == current.tenant_id)
    if case_status:
        q = q.where(Case.status == case_status)
    if player_id:
        q = q.where(Case.player_id == player_id)
    if assigned_to:
        q = q.where(Case.assigned_to == assigned_to)
    q = q.order_by(Case.created_at.desc())

    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()
    offset = (page - 1) * size
    result = await db.execute(q.offset(offset).limit(size))
    cases = result.scalars().all()
    return CaseListResponse(
        items=[CaseResponse.model_validate(c) for c in cases], total=total, page=page, size=size
    )


@router.post("", response_model=CaseResponse, status_code=status.HTTP_201_CREATED)
async def create_case(
    body: CaseCreateRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseResponse:
    case = Case(
        tenant_id=current.tenant_id,
        title=body.title,
        description=body.description,
        player_id=body.player_id,
        status=CaseStatus.OPEN,
    )
    db.add(case)
    await db.flush()
    await db.refresh(case)

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="CASE_CREATED",
        entity_type="Case",
        entity_id=str(case.id),
        new_values={"title": case.title, "status": case.status.value},
    )
    db.add(audit)
    return CaseResponse.model_validate(case)


@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case(
    case_id: uuid.UUID,
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseDetailResponse:
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(Case)
        .options(
            selectinload(Case.events),
            selectinload(Case.evidence_files),
            selectinload(Case.report_packages),
        )
        .where(Case.id == case_id, Case.tenant_id == current.tenant_id)
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return CaseDetailResponse.model_validate(case)


@router.put("/{case_id}", response_model=CaseResponse)
async def update_case(
    case_id: uuid.UUID,
    body: CaseUpdateRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseResponse:
    case = await _get_case_or_404(db, case_id, current.tenant_id)
    old_status = case.status.value

    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(case, field, value)

    if body.status and body.status.value != old_status:
        audit = AuditLog(
            tenant_id=current.tenant_id,
            user_id=current.user.id,
            action="CASE_STATUS_CHANGED",
            entity_type="Case",
            entity_id=str(case.id),
            old_values={"status": old_status},
            new_values={"status": body.status.value},
        )
        db.add(audit)

    await db.flush()
    await db.refresh(case)
    return CaseResponse.model_validate(case)


@router.post("/{case_id}/assign", response_model=CaseResponse)
async def assign_case(
    case_id: uuid.UUID,
    body: CaseAssignRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseResponse:
    case = await _get_case_or_404(db, case_id, current.tenant_id)
    old_assigned = str(case.assigned_to) if case.assigned_to else None
    case.assigned_to = body.user_id

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="CASE_ASSIGNED",
        entity_type="Case",
        entity_id=str(case.id),
        old_values={"assigned_to": old_assigned},
        new_values={"assigned_to": str(body.user_id)},
    )
    db.add(audit)
    await db.flush()
    await db.refresh(case)
    return CaseResponse.model_validate(case)


@router.post("/{case_id}/events", response_model=CaseEventResponse, status_code=status.HTTP_201_CREATED)
async def add_case_event(
    case_id: uuid.UUID,
    body: CaseEventCreateRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseEventResponse:
    await _get_case_or_404(db, case_id, current.tenant_id)

    event = CaseEvent(
        case_id=case_id,
        user_id=current.user.id,
        event_type=body.event_type,
        content=body.content,
        event_metadata=body.event_metadata,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)
    return CaseEventResponse.model_validate(event)


@router.post("/{case_id}/evidence", response_model=EvidenceResponse, status_code=status.HTTP_201_CREATED)
async def upload_evidence(
    case_id: uuid.UUID,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> EvidenceResponse:
    await _get_case_or_404(db, case_id, current.tenant_id)

    content = await file.read()
    file_size = len(content)
    file_path = f"cases/{case_id}/{file.filename}"

    # Store in MinIO if available, otherwise just record the path
    if _S3_AVAILABLE:
        try:
            from app.config import settings

            s3 = boto3.client(
                "s3",
                endpoint_url=f"http://{settings.MINIO_ENDPOINT}",
                aws_access_key_id=settings.MINIO_ACCESS_KEY,
                aws_secret_access_key=settings.MINIO_SECRET_KEY,
            )
            s3.put_object(Bucket=settings.MINIO_BUCKET, Key=file_path, Body=content)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("MinIO upload failed for %s: %s", file_path, exc)
        except Exception as exc:
            logger.error("Unexpected error during MinIO upload for %s: %s", file_path, exc, exc_info=True)
    else:
        logger.warning("boto3 not available; skipping MinIO upload for %s", file_path)

    evidence = Evidence(
        case_id=case_id,
        uploaded_by=current.user.id,
        file_name=file.filename,
        file_path=file_path,
        file_size=file_size,
    )
    db.add(evidence)

    case_event = CaseEvent(
        case_id=case_id,
        user_id=current.user.id,
        event_type=CaseEventType.EVIDENCE_ADDED,
        content=f"Evidence uploaded: {file.filename}",
    )
    db.add(case_event)
    await db.flush()
    await db.refresh(evidence)
    return EvidenceResponse.model_validate(evidence)


@router.post("/{case_id}/report-package", response_model=ReportPackageResponse, status_code=status.HTTP_201_CREATED)
async def generate_report_package(
    case_id: uuid.UUID,
    body: ReportPackageRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReportPackageResponse:
    case = await _get_case_or_404(db, case_id, current.tenant_id)

    payload: dict = {
        "case_id": str(case_id),
        "tenant_id": str(current.tenant_id),
        "title": case.title,
        "player_id": case.player_id,
        "player_data": body.player_data,
        "events_data": body.events_data,
        "rules_data": body.rules_data,
        "analyst_justification": body.analyst_justification,
        "export_format": body.export_format.value,
    }

    report = ReportPackage(
        case_id=case_id,
        tenant_id=current.tenant_id,
        generated_by=current.user.id,
        player_data=body.player_data,
        events_data=body.events_data,
        rules_data=body.rules_data,
        analyst_justification=body.analyst_justification,
        export_format=body.export_format,
        payload=payload,
    )
    db.add(report)

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="REPORT_PACKAGE_GENERATED",
        entity_type="ReportPackage",
        entity_id=str(case_id),
        new_values={"case_id": str(case_id), "format": body.export_format.value},
    )
    db.add(audit)
    await db.flush()
    await db.refresh(report)
    return ReportPackageResponse.model_validate(report)
