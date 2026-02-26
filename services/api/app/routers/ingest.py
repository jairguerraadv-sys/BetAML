import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, require_analyst_or_admin, require_any_role
from app.models.ingest import IngestJob, IngestStatus
from app.schemas.ingest import (
    IngestBatchRequest,
    IngestBatchResponse,
    IngestEventRequest,
    IngestEventResponse,
    IngestFileResponse,
    IngestJobListResponse,
    IngestJobResponse,
)
from app.services import kafka_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/file", response_model=IngestFileResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_file(
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    source_system: Optional[str] = Header(None, alias="x-source-system"),
    mapping_config_id: Optional[str] = Header(None, alias="x-mapping-config-id"),
) -> IngestFileResponse:
    if not source_system:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="x-source-system header required")

    mapping_id: Optional[uuid.UUID] = None
    if mapping_config_id:
        try:
            mapping_id = uuid.UUID(mapping_config_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid mapping_config_id")

    job = IngestJob(
        tenant_id=current.tenant_id,
        source_system=source_system,
        mapping_config_id=mapping_id,
        file_name=file.filename,
        status=IngestStatus.QUEUED,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    kafka_service.publish(
        topic="ingest.jobs",
        key=str(job.id),
        value={
            "job_id": str(job.id),
            "tenant_id": str(current.tenant_id),
            "source_system": source_system,
            "mapping_config_id": str(mapping_id) if mapping_id else None,
            "file_name": file.filename,
        },
    )

    return IngestFileResponse(job_id=job.id, status=job.status)


@router.post("/event", response_model=IngestEventResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    body: IngestEventRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
) -> IngestEventResponse:
    event_id = str(uuid.uuid4())
    kafka_service.publish(
        topic="ingest.events",
        key=event_id,
        value={
            "event_id": event_id,
            "tenant_id": str(current.tenant_id),
            "source_system": body.source_system,
            "source_event_id": body.source_event_id,
            "entity_type": body.entity_type.value,
            "occurred_at": body.occurred_at.isoformat(),
            "payload": body.payload,
        },
    )
    return IngestEventResponse(event_id=event_id)


@router.post("/batch", response_model=IngestBatchResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest_batch(
    body: IngestBatchRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
) -> IngestBatchResponse:
    event_ids: list[str] = []
    for event in body.events:
        event_id = str(uuid.uuid4())
        kafka_service.publish(
            topic="ingest.events",
            key=event_id,
            value={
                "event_id": event_id,
                "tenant_id": str(current.tenant_id),
                "source_system": event.source_system,
                "source_event_id": event.source_event_id,
                "entity_type": event.entity_type.value,
                "occurred_at": event.occurred_at.isoformat(),
                "payload": event.payload,
            },
        )
        event_ids.append(event_id)
    return IngestBatchResponse(count=len(event_ids), event_ids=event_ids)


@router.get("/jobs", response_model=IngestJobListResponse)
async def list_jobs(
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    size: int = 20,
) -> IngestJobListResponse:
    offset = (page - 1) * size
    q = select(IngestJob).where(IngestJob.tenant_id == current.tenant_id).order_by(IngestJob.created_at.desc())
    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()
    result = await db.execute(q.offset(offset).limit(size))
    jobs = result.scalars().all()
    return IngestJobListResponse(items=[IngestJobResponse.model_validate(j) for j in jobs], total=total)
