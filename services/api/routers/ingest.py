"""routers/ingest.py — ingest event/batch/file/jobs + streaming + webhook connectors."""
from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from config import settings
from database import AsyncSessionLocal, get_db
from libs.connectors import (
    EPSILON_SIGNATURE_HEADER,
    EPSILON_TIMESTAMP_HEADER,
    ConnectorEpsilon,
    get_connector,
)
from models import IngestError, IngestJob, MappingConfig, SystemFlag, User
from utils import get_producer, redis_rate_limit

try:
    from minio import Minio
except Exception:  # pragma: no cover
    Minio = None

logger = structlog.get_logger(__name__)

ALLOWED_SOURCE_SYSTEMS = frozenset(
    {
        "BackofficeAlpha",
        "BackofficeBeta",
        "SportsBook",
        "CasinoEngine",
        "ConnectorGamma",
        "ConnectorDelta",
        "ConnectorEpsilon",
    }
)

router = APIRouter(tags=["ingest"])


class IngestEventRequest(BaseModel):
    source_system: str
    entity_type: str
    source_event_id: Optional[str] = None
    payload: dict[str, Any]
    mapping_config_id: Optional[str] = None


class ReprocessRequest(BaseModel):
    mapping_version_id: Optional[str] = None
    reason: str = "manual_reprocess"


class WebsocketIngestRequest(BaseModel):
    source_system: str
    entity_type: str
    payload: dict[str, Any]
    source_event_id: Optional[str] = None


class ResolveIngestErrorRequest(BaseModel):
    note: Optional[str] = None


class ReplayIngestErrorRequest(BaseModel):
    corrected_payload: dict[str, Any]
    entity_type: Optional[str] = None
    mapping_config_id: Optional[str] = None
    resolve_original: bool = True
    note: Optional[str] = None


class ConnectorParseSummary(BaseModel):
    accepted: int
    failed: int
    total: int
    errors: list[dict[str, Any]]


async def _publish_with_retries(
    *,
    producer: Any,
    topic: str,
    payload: dict[str, Any],
    key: str,
    tenant_id: str,
    source_system: str,
    context: dict[str, Any] | None = None,
) -> bool:
    max_retries = int(getattr(settings, "dlq_max_retries", 3) or 3)
    for attempt in range(1, max_retries + 1):
        try:
            await producer.send(topic, payload, key=key)
            return True
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                try:
                    await producer.send(
                        f"{topic}.dlq",
                        {
                            "tenant_id": tenant_id,
                            "source_system": source_system,
                            "target_topic": topic,
                            "reason": str(exc),
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                            "payload": payload,
                            "context": context or {},
                        },
                        key=key,
                    )
                except Exception as dlq_exc:  # noqa: BLE001
                    logger.error("ingest_dlq_publish_failed", error=str(dlq_exc), topic=topic)
                return False
            await asyncio.sleep(0.1 * attempt)
    return False


def _get_minio_client() -> Minio | None:
    if Minio is None:
        return None
    endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
    secure = settings.minio_endpoint.startswith("https://")
    return Minio(
        endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )


def _upload_bronze_file(*, tenant_id: str, job_id: str, file_name: str, content: bytes) -> str | None:
    client = _get_minio_client()
    if client is None:
        return None

    bucket = settings.minio_bucket
    object_name = f"bronze/{tenant_id}/ingest_jobs/{job_id}/{file_name}"
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

        from io import BytesIO

        stream = BytesIO(content)
        client.put_object(
            bucket,
            object_name,
            stream,
            length=len(content),
            content_type="text/csv",
        )
        return object_name
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_file_bronze_upload_failed", error=str(exc), job_id=job_id)
        return None


async def _tenant_ingest_rate_limit(db: AsyncSession, tenant_id: str, default_limit: int) -> int:
    # Compatibility: support both schemas below.
    # New schema: tenant_id + flag_name + flag_value
    # Legacy schema: key + value (global)
    try:
        if all(hasattr(SystemFlag, attr) for attr in ("tenant_id", "flag_name", "flag_value")):
            stmt = select(SystemFlag).where(
                SystemFlag.tenant_id == tenant_id,
                SystemFlag.flag_name == "ingest_rate_limit_per_min",
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                return default_limit
            return max(1, int(row.flag_value))

        if all(hasattr(SystemFlag, attr) for attr in ("key", "value")):
            stmt = select(SystemFlag).where(SystemFlag.key == "ingest_rate_limit_per_min")
            row = (await db.execute(stmt)).scalar_one_or_none()
            if not row:
                return default_limit
            return max(1, int(row.value))
    except Exception:
        return default_limit

    return default_limit


def _build_envelope(
    *,
    tenant_id: str,
    source_system: str,
    entity_type: str,
    payload: dict[str, Any],
    source_event_id: str,
    mapping_config_id: str | None = None,
    ingest_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "source_system": source_system,
        "source_event_id": source_event_id,
        "schema_version": 1,
        "entity_type": entity_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "raw_payload": payload,
        "mapping_config_id": mapping_config_id,
        "ingest_metadata": {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "mapper_version": "1.0",
            **(ingest_metadata or {}),
        },
    }


@router.post("/ingest/event", status_code=202)
async def ingest_event(
    body: IngestEventRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    max_requests = await _tenant_ingest_rate_limit(db, current_user.tenant_id, default_limit=300)
    await redis_rate_limit(current_user.tenant_id, "ingest.event", max_requests=max_requests)

    if body.source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{body.source_system}' não reconhecido. Permitidos: {sorted(ALLOWED_SOURCE_SYSTEMS)}")

    source_event_id = body.source_event_id or str(uuid.uuid4())
    envelope = _build_envelope(
        tenant_id=current_user.tenant_id,
        source_system=body.source_system,
        entity_type=body.entity_type,
        payload=body.payload,
        source_event_id=source_event_id,
        mapping_config_id=body.mapping_config_id,
    )
    producer = await get_producer()
    if producer:
        topic = f"raw.{body.entity_type.lower()}s"
        ok = await _publish_with_retries(
            producer=producer,
            topic=topic,
            payload=envelope,
            key=source_event_id,
            tenant_id=current_user.tenant_id,
            source_system=body.source_system,
            context={"endpoint": "/ingest/event"},
        )
        if not ok:
            raise HTTPException(503, "Falha ao enfileirar evento após retries; enviado para DLQ")
    return {"event_id": envelope["event_id"], "status": "queued"}


@router.post("/ingest/batch", status_code=202)
async def ingest_batch(
    events: list[IngestEventRequest],
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    max_requests = await _tenant_ingest_rate_limit(db, current_user.tenant_id, default_limit=50)
    await redis_rate_limit(current_user.tenant_id, "ingest.batch", max_requests=max_requests)

    producer = await get_producer()
    results = []
    for body in events:
        if body.source_system not in ALLOWED_SOURCE_SYSTEMS:
            results.append({
                "status": "rejected",
                "reason": f"source_system inválido: {body.source_system}",
            })
            continue

        source_event_id = body.source_event_id or str(uuid.uuid4())
        envelope = _build_envelope(
            tenant_id=current_user.tenant_id,
            source_system=body.source_system,
            entity_type=body.entity_type,
            payload=body.payload,
            source_event_id=source_event_id,
            mapping_config_id=body.mapping_config_id,
        )
        if producer:
            topic = f"raw.{body.entity_type.lower()}s"
            ok = await _publish_with_retries(
                producer=producer,
                topic=topic,
                payload=envelope,
                key=source_event_id,
                tenant_id=current_user.tenant_id,
                source_system=body.source_system,
                context={"endpoint": "/ingest/batch"},
            )
            if not ok:
                results.append({"event_id": envelope["event_id"], "status": "failed_dlq"})
                continue
        results.append({"event_id": envelope["event_id"], "status": "queued"})

    return {"count": len(results), "results": results}


@router.post("/ingest/file", status_code=202)
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_system: str = Form(...),
    mapping_config_id: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    _ = background_tasks
    max_requests = await _tenant_ingest_rate_limit(db, current_user.tenant_id, default_limit=20)
    await redis_rate_limit(current_user.tenant_id, "ingest.file", max_requests=max_requests)

    content = await file.read()

    if source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{source_system}' não reconhecido. Permitidos: {sorted(ALLOWED_SOURCE_SYSTEMS)}")

    lines = [ln for ln in content.decode(errors="replace").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise HTTPException(400, "CSV inválido: arquivo deve conter ao menos uma linha de dados além do cabeçalho")

    mapping_version_id = None
    if mapping_config_id:
        mc = await db.get(MappingConfig, mapping_config_id)
        if not mc or mc.tenant_id != current_user.tenant_id:
            raise HTTPException(404, "MappingConfig não encontrado para o tenant")
        if not mc.is_current:
            mapping_version_id = mc.id

    job = IngestJob(
        tenant_id=current_user.tenant_id,
        source_system=source_system,
        mapping_config_id=mapping_config_id,
        mapping_version_id=mapping_version_id,
        file_name=file.filename,
        file_size_bytes=len(content),
        file_path=None,
        bytes_processed=0,
        status="QUEUED",
        created_by=current_user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    bronze_path = _upload_bronze_file(
        tenant_id=current_user.tenant_id,
        job_id=job.id,
        file_name=file.filename or "ingest.csv",
        content=content,
    )
    if bronze_path:
        job.file_path = bronze_path
        await db.commit()
        await db.refresh(job)

    producer = await get_producer()
    if producer:
        msg = {
            "job_id": job.id,
            "tenant_id": current_user.tenant_id,
            "source_system": source_system,
            "mapping_config_id": mapping_config_id,
            "mapping_version_id": mapping_version_id,
            "file_name": file.filename,
            "file_path": job.file_path,
            "file_content_b64": base64.b64encode(content).decode(),
        }
        ok = await _publish_with_retries(
            producer=producer,
            topic="ingest.jobs",
            payload=msg,
            key=job.id,
            tenant_id=current_user.tenant_id,
            source_system=source_system,
            context={"endpoint": "/ingest/file", "job_id": job.id},
        )
        if not ok:
            raise HTTPException(503, "Falha ao enfileirar job de ingestão após retries; enviado para DLQ")

    return {"job_id": job.id, "status": "QUEUED", "file_name": file.filename}


@router.post("/ingest/webhook/epsilon", status_code=202)
async def ingest_epsilon_webhook(
    request: Request,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    max_requests = await _tenant_ingest_rate_limit(db, current_user.tenant_id, default_limit=300)
    await redis_rate_limit(current_user.tenant_id, "ingest.webhook.epsilon", max_requests=max_requests)

    body = await request.body()
    connector = ConnectorEpsilon(signing_secret=settings.jwt_secret)
    result = connector.parse(
        body,
        headers={
            EPSILON_SIGNATURE_HEADER: request.headers.get(EPSILON_SIGNATURE_HEADER, ""),
            EPSILON_TIMESTAMP_HEADER: request.headers.get(EPSILON_TIMESTAMP_HEADER, ""),
        },
    )
    if not result.success:
        raise HTTPException(400, f"Webhook inválido: {result.errors}")

    producer = await get_producer()
    queued = 0
    for rec in result.records:
        source_event_id = rec.get("event_id") or str(uuid.uuid4())
        envelope = _build_envelope(
            tenant_id=current_user.tenant_id,
            source_system="ConnectorEpsilon",
            entity_type="TRANSACTION",
            payload=rec,
            source_event_id=source_event_id,
            ingest_metadata={"channel": "webhook", "webhook": "epsilon"},
        )
        if producer:
            ok = await _publish_with_retries(
                producer=producer,
                topic="raw.transactions",
                payload=envelope,
                key=source_event_id,
                tenant_id=current_user.tenant_id,
                source_system="ConnectorEpsilon",
                context={"endpoint": "/ingest/webhook/epsilon"},
            )
            if not ok:
                continue
        queued += 1

    return {"status": "accepted", "count": queued}


@router.get("/ingest/jobs")
async def list_ingest_jobs(
    status_filter: Optional[str] = Query(None, alias="status"),
    source_system: Optional[str] = Query(None),
    tenant: Optional[str] = Query(None),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    effective_tenant = current_user.tenant_id
    if tenant and tenant != current_user.tenant_id:
        raise HTTPException(403, "Filtro tenant só pode usar o tenant autenticado")

    filters = [IngestJob.tenant_id == effective_tenant]
    if status_filter:
        filters.append(IngestJob.status == status_filter)
    if source_system:
        filters.append(IngestJob.source_system == source_system)
    if from_date:
        filters.append(IngestJob.created_at >= from_date)
    if to_date:
        filters.append(IngestJob.created_at <= to_date)

    q = (
        select(IngestJob)
        .where(and_(*filters))
        .order_by(desc(IngestJob.created_at))
        .limit(limit)
        .offset(offset)
    )
    jobs = (await db.execute(q)).scalars().all()

    return [
        {
            "id": j.id,
            "source_system": j.source_system,
            "file_name": j.file_name,
            "status": j.status,
            "total_records": j.total_records,
            "processed_records": j.processed_records,
            "failed_records": j.failed_records,
            "bytes_processed": j.bytes_processed,
            "duration_ms": j.duration_ms,
            "created_at": j.created_at,
            "updated_at": j.updated_at,
        }
        for j in jobs
    ]


@router.get("/ingest/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    j = await db.get(IngestJob, job_id)
    if not j or j.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Job não encontrado")

    err_count_stmt = select(func.count()).where(
        IngestError.tenant_id == current_user.tenant_id,
        IngestError.ingest_job_id == j.id,
    )
    err_count = (await db.execute(err_count_stmt)).scalar_one()

    err_sample_stmt = (
        select(IngestError)
        .where(
            IngestError.tenant_id == current_user.tenant_id,
            IngestError.ingest_job_id == j.id,
        )
        .order_by(desc(IngestError.created_at))
        .limit(10)
    )
    err_sample = (await db.execute(err_sample_stmt)).scalars().all()

    return {
        "id": j.id,
        "source_system": j.source_system,
        "file_name": j.file_name,
        "status": j.status,
        "total_records": j.total_records,
        "processed_records": j.processed_records,
        "failed_records": j.failed_records,
        "error_count": err_count,
        "error_sample": [
            {
                "id": e.id,
                "line_number": e.line_number,
                "error_reason": e.error_reason,
                "raw_payload": e.raw_payload,
                "created_at": e.created_at,
            }
            for e in err_sample
        ],
        "file_size_bytes": j.file_size_bytes,
        "bytes_processed": j.bytes_processed,
        "duration_ms": j.duration_ms,
        "reprocessed_from": j.reprocessed_from,
        "mapping_version_id": j.mapping_version_id,
        "file_path": j.file_path,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


@router.get("/ingest/errors")
async def list_ingest_errors(
    job_id: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(None),
    source_system: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    filters = [IngestError.tenant_id == current_user.tenant_id]
    if job_id:
        filters.append(IngestError.ingest_job_id == job_id)
    if resolved is not None:
        filters.append(IngestError.resolved == resolved)
    if source_system:
        filters.append(IngestError.source_system == source_system)

    stmt = (
        select(IngestError)
        .where(and_(*filters))
        .order_by(desc(IngestError.created_at))
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return [
        {
            "id": e.id,
            "ingest_job_id": e.ingest_job_id,
            "source_system": e.source_system,
            "entity_type": e.entity_type,
            "line_number": e.line_number,
            "error_reason": e.error_reason,
            "error_detail": e.error_detail,
            "raw_payload": e.raw_payload,
            "resolved": e.resolved,
            "resolved_by": e.resolved_by,
            "resolved_at": e.resolved_at,
            "created_at": e.created_at,
        }
        for e in rows
    ]


@router.post("/ingest/errors/{error_id}/resolve")
async def resolve_ingest_error(
    error_id: str,
    body: ResolveIngestErrorRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    err = await db.get(IngestError, error_id)
    if not err or err.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Erro de ingestão não encontrado")

    err.resolved = True
    err.resolved_by = current_user.id
    err.resolved_at = datetime.now(timezone.utc)
    err.error_detail = {
        **(err.error_detail or {}),
        "resolution_note": body.note,
    }
    await db.commit()
    return {"status": "resolved", "id": err.id}


@router.post("/ingest/errors/{error_id}/replay", status_code=202)
async def replay_ingest_error(
    error_id: str,
    body: ReplayIngestErrorRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    err = await db.get(IngestError, error_id)
    if not err or err.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Erro de ingestão não encontrado")

    if err.source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{err.source_system}' não suportado para replay")

    mapping_config_id = body.mapping_config_id
    if mapping_config_id:
        mc = await db.get(MappingConfig, mapping_config_id)
        if not mc or mc.tenant_id != current_user.tenant_id:
            raise HTTPException(404, "MappingConfig não encontrado para o tenant")

    entity_type = (body.entity_type or err.entity_type or "TRANSACTION").upper()
    source_event_id = str(body.corrected_payload.get("event_id") or uuid.uuid4())

    producer = await get_producer()
    if not producer:
        raise HTTPException(503, "Kafka indisponível para replay do erro")

    envelope = _build_envelope(
        tenant_id=current_user.tenant_id,
        source_system=err.source_system,
        entity_type=entity_type,
        payload=body.corrected_payload,
        source_event_id=source_event_id,
        mapping_config_id=mapping_config_id,
        ingest_metadata={
            "channel": "quarantine_replay",
            "ingest_error_id": err.id,
            "replayed_by": current_user.id,
            "original_line_number": err.line_number,
        },
    )
    topic = f"raw.{entity_type.lower()}s"
    ok = await _publish_with_retries(
        producer=producer,
        topic=topic,
        payload=envelope,
        key=source_event_id,
        tenant_id=current_user.tenant_id,
        source_system=err.source_system,
        context={"endpoint": "/ingest/errors/{error_id}/replay", "ingest_error_id": err.id},
    )
    if not ok:
        raise HTTPException(503, "Falha ao reenfileirar erro após retries; enviado para DLQ")

    err.error_detail = {
        **(err.error_detail or {}),
        "replay": {
            "replayed_at": datetime.now(timezone.utc).isoformat(),
            "replayed_by": current_user.id,
            "event_id": envelope["event_id"],
            "source_event_id": source_event_id,
            "entity_type": entity_type,
            "mapping_config_id": mapping_config_id,
            "note": body.note,
        },
    }
    if body.resolve_original:
        err.resolved = True
        err.resolved_by = current_user.id
        err.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    return {
        "status": "queued",
        "event_id": envelope["event_id"],
        "source_event_id": source_event_id,
        "ingest_error_id": err.id,
        "resolved": err.resolved,
    }


@router.post("/ingest/jobs/{job_id}/reprocess", status_code=202)
async def reprocess_job(
    job_id: str,
    body: ReprocessRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(IngestJob, job_id)
    if not job or job.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Job não encontrado")

    if job.status not in ("FAILED", "PARTIAL", "DONE"):
        raise HTTPException(409, f"Status {job.status} não permite reprocessamento")

    mapping_version_id = body.mapping_version_id or job.mapping_version_id or job.mapping_config_id
    if mapping_version_id:
        mc = await db.get(MappingConfig, mapping_version_id)
        if not mc or mc.tenant_id != current_user.tenant_id:
            raise HTTPException(404, "mapping_version_id inválido para este tenant")

    producer = await get_producer()
    if not producer:
        raise HTTPException(503, "Kafka indisponível para reprocessamento")
    if not job.file_path:
        raise HTTPException(409, "Job original sem arquivo Bronze para reprocessamento")

    new_job = IngestJob(
        tenant_id=job.tenant_id,
        source_system=job.source_system,
        mapping_config_id=job.mapping_config_id,
        mapping_version_id=mapping_version_id,
        connector_type=job.connector_type,
        file_name=job.file_name,
        file_size_bytes=job.file_size_bytes,
        file_path=job.file_path,
        status="QUEUED",
        reprocessed_from=job.id,
        created_by=current_user.id,
        error_message=f"reprocess_reason={body.reason}",
    )
    db.add(new_job)
    await db.commit()
    await db.refresh(new_job)

    msg = {
        "job_id": new_job.id,
        "tenant_id": current_user.tenant_id,
        "source_system": new_job.source_system,
        "mapping_config_id": new_job.mapping_config_id,
        "mapping_version_id": new_job.mapping_version_id,
        "file_name": new_job.file_name,
        "file_path": new_job.file_path,
    }
    ok = await _publish_with_retries(
        producer=producer,
        topic="ingest.jobs",
        payload=msg,
        key=new_job.id,
        tenant_id=current_user.tenant_id,
        source_system=new_job.source_system,
        context={"endpoint": "/ingest/jobs/{job_id}/reprocess", "job_id": new_job.id},
    )
    if not ok:
        new_job.status = "FAILED"
        new_job.error_message = "enqueue_failed_after_retries"
        await db.commit()
        raise HTTPException(503, "Falha ao enfileirar reprocessamento após retries; enviado para DLQ")

    return {"job_id": new_job.id, "status": "QUEUED"}


@router.websocket("/ingest/ws")
async def ingest_websocket(websocket: WebSocket):
    """Canal websocket para ingestão contínua com backpressure por fila limitada."""
    await websocket.accept()

    auth_header = websocket.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        await websocket.send_json({"error": "missing_bearer_token"})
        await websocket.close(code=1008)
        return

    token = auth_header[7:]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("sub")
        tenant_id = payload.get("tenant_id")
        if not user_id or not tenant_id:
            raise JWTError("invalid token payload")
    except JWTError:
        await websocket.send_json({"error": "invalid_token"})
        await websocket.close(code=1008)
        return

    # Verificar blacklist de JWT (tokens revogados via /auth/logout)
    jti = payload.get("jti")
    if jti:
        try:
            from auth import _get_auth_redis
            r = await _get_auth_redis()
            if r and await r.exists(f"betaml:revoked:jti:{jti}"):
                await websocket.send_json({"error": "token_revoked"})
                await websocket.close(code=1008)
                return
        except Exception:
            pass  # Redis indisponível não bloqueia o WS

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if not user or not user.active:
            await websocket.send_json({"error": "inactive_user"})
            await websocket.close(code=1008)
            return
        if user.role not in {"ADMIN", "AML_ANALYST"}:
            await websocket.send_json({"error": "insufficient_role"})
            await websocket.close(code=1008)
            return

        max_requests = await _tenant_ingest_rate_limit(db, tenant_id, default_limit=600)

    producer = await get_producer()
    if not producer:
        await websocket.send_json({"error": "kafka_unavailable"})
        await websocket.close(code=1011)
        return

    queue: asyncio.Queue[WebsocketIngestRequest] = asyncio.Queue(maxsize=500)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                source_event_id = item.source_event_id or str(uuid.uuid4())
                envelope = _build_envelope(
                    tenant_id=tenant_id,
                    source_system=item.source_system,
                    entity_type=item.entity_type,
                    payload=item.payload,
                    source_event_id=source_event_id,
                    ingest_metadata={"channel": "websocket"},
                )
                topic = f"raw.{item.entity_type.lower()}s"
                ok = await _publish_with_retries(
                    producer=producer,
                    topic=topic,
                    payload=envelope,
                    key=source_event_id,
                    tenant_id=tenant_id,
                    source_system=item.source_system,
                    context={"endpoint": "/ingest/ws"},
                )
                if not ok:
                    await websocket.send_json({"status": "failed_dlq", "event_id": envelope["event_id"]})
                else:
                    await websocket.send_json({"status": "queued", "event_id": envelope["event_id"]})
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"status": "failed", "error": str(exc)})
            finally:
                queue.task_done()

    worker_task = asyncio.create_task(worker())
    try:
        while True:
            raw_msg = await websocket.receive_json()
            try:
                msg = WebsocketIngestRequest(**raw_msg)
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"status": "invalid", "error": str(exc)})
                continue

            if msg.source_system not in ALLOWED_SOURCE_SYSTEMS:
                await websocket.send_json({"status": "invalid", "error": "source_system não reconhecido"})
                continue

            await redis_rate_limit(tenant_id, "ingest.ws", max_requests=max_requests)

            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:
                await websocket.send_json({
                    "status": "backpressure",
                    "detail": "fila temporariamente cheia",
                    "queued": queue.qsize(),
                    "retry_ms": 500,
                })
    except WebSocketDisconnect:
        logger.info("ingest_ws_disconnected", tenant_id=tenant_id)
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except Exception:
            pass


@router.post("/ingest/connectors/{connector_name}/parse", status_code=202)
async def parse_connector_payload(
    connector_name: str,
    file: UploadFile = File(...),
    entity_type: str = Form("TRANSACTION"),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    """Dedicated parser endpoint for Gamma/XML and Delta/NDJSON connectors.

    Validates connector payload, quarantines parse errors and enqueues valid events.
    """
    name = connector_name.strip().lower()
    if name not in {"gamma", "delta"}:
        raise HTTPException(400, "connector_name deve ser 'gamma' ou 'delta' para este endpoint")

    max_requests = await _tenant_ingest_rate_limit(db, current_user.tenant_id, default_limit=120)
    await redis_rate_limit(current_user.tenant_id, f"ingest.connector.{name}", max_requests=max_requests)

    content = await file.read()
    source_system = "ConnectorGamma" if name == "gamma" else "ConnectorDelta"
    connector_kwargs = {"root_tag": "transaction"} if name == "gamma" else {}
    connector = get_connector(name, **connector_kwargs)
    parse_result = connector.parse(content, entity_type=entity_type)

    # Register an ingest job for observability and reprocessing trail
    job = IngestJob(
        tenant_id=current_user.tenant_id,
        source_system=source_system,
        connector_type="FILE",
        file_name=file.filename,
        file_size_bytes=len(content),
        total_records=parse_result.total,
        processed_records=0,
        failed_records=parse_result.failed,
        bytes_processed=0,
        status="PROCESSING",
        created_by=current_user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    bronze_path = _upload_bronze_file(
        tenant_id=current_user.tenant_id,
        job_id=job.id,
        file_name=file.filename or f"{name}.payload",
        content=content,
    )
    if bronze_path:
        job.file_path = bronze_path
        await db.commit()

    producer = await get_producer()
    accepted = 0
    failed = 0
    error_rows: list[dict[str, Any]] = []

    for rec in parse_result.records:
        source_event_id = str(rec.get("event_id") or uuid.uuid4())
        envelope = _build_envelope(
            tenant_id=current_user.tenant_id,
            source_system=source_system,
            entity_type=entity_type,
            payload=rec,
            source_event_id=source_event_id,
            ingest_metadata={"channel": "connector-parse", "job_id": job.id},
        )
        topic = f"raw.{entity_type.lower()}s"
        if producer:
            ok = await _publish_with_retries(
                producer=producer,
                topic=topic,
                payload=envelope,
                key=source_event_id,
                tenant_id=current_user.tenant_id,
                source_system=source_system,
                context={"endpoint": "/ingest/connectors/{connector_name}/parse", "job_id": job.id},
            )
            if not ok:
                failed += 1
                error_rows.append({"line": None, "reason": "publish_failed_after_retries", "raw": rec})
                continue
        accepted += 1

    for err in parse_result.errors:
        failed += 1
        reason = err.get("reason", "parse_error") if isinstance(err, dict) else str(err)
        raw_payload = err.get("raw", "") if isinstance(err, dict) else ""
        line_number = err.get("line") if isinstance(err, dict) else None
        error_rows.append({"line": line_number, "reason": reason, "raw": raw_payload})
        db.add(
            IngestError(
                tenant_id=current_user.tenant_id,
                ingest_job_id=job.id,
                source_system=source_system,
                entity_type=entity_type,
                raw_payload=str(raw_payload),
                error_reason=reason,
                error_detail={"line": line_number, "connector": name},
                line_number=line_number,
                resolved=False,
            )
        )

    job.processed_records = accepted
    job.failed_records = failed
    job.bytes_processed = len(content)
    job.error_sample = error_rows[:10]
    job.status = "DONE" if failed == 0 else ("PARTIAL" if accepted > 0 else "FAILED")
    await db.commit()

    return {
        "job_id": job.id,
        "source_system": source_system,
        "status": job.status,
        "summary": ConnectorParseSummary(
            accepted=accepted,
            failed=failed,
            total=parse_result.total,
            errors=error_rows[:20],
        ).model_dump(),
    }
