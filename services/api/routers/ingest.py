"""routers/ingest.py — ingest event/batch/file/jobs + streaming + webhook connectors."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional, cast

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
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, model_validator
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import IngestPrincipal, get_ingest_principal, require_roles
from config import settings
from database import AsyncSessionLocal, get_db
from libs.connectors import (
    EPSILON_SIGNATURE_HEADER,
    EPSILON_TIMESTAMP_HEADER,
    ConnectorEpsilon,
    get_connector,
)
from libs.mapping import MappingEngine, get_default_mapping
from models import IngestError, IngestJob, MappingConfig, ScoringConfig, SystemFlag, User
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

_INGEST_WS_RUNTIME: dict[str, dict[str, Any]] = {}


def _ensure_ws_runtime(tenant_id: str) -> dict[str, Any]:
    state = _INGEST_WS_RUNTIME.get(tenant_id)
    if state is None:
        state = {
            "active_connections": 0,
            "queued_messages": 0,
            "peak_queue_depth": 0,
            "backpressure_events": 0,
            "last_backpressure_at": None,
            "messages_queued_total": 0,
            "messages_acked_total": 0,
            "max_queue_size": 500,
        }
        _INGEST_WS_RUNTIME[tenant_id] = state
    return state


def _ws_runtime_connected(tenant_id: str, *, max_queue_size: int) -> None:
    state = _ensure_ws_runtime(tenant_id)
    state["active_connections"] += 1
    state["max_queue_size"] = max_queue_size


def _ws_runtime_disconnected(tenant_id: str) -> None:
    state = _ensure_ws_runtime(tenant_id)
    state["active_connections"] = max(0, int(state["active_connections"]) - 1)
    if state["active_connections"] == 0:
        state["queued_messages"] = 0


def _ws_runtime_enqueued(tenant_id: str, *, queue_size: int) -> None:
    state = _ensure_ws_runtime(tenant_id)
    state["queued_messages"] = queue_size
    state["messages_queued_total"] += 1
    state["peak_queue_depth"] = max(int(state["peak_queue_depth"]), queue_size)


def _ws_runtime_acked(tenant_id: str, *, queue_size: int) -> None:
    state = _ensure_ws_runtime(tenant_id)
    state["queued_messages"] = max(0, queue_size)
    state["messages_acked_total"] += 1


def _ws_runtime_backpressure(tenant_id: str, *, queue_size: int) -> None:
    state = _ensure_ws_runtime(tenant_id)
    state["queued_messages"] = queue_size
    state["peak_queue_depth"] = max(int(state["peak_queue_depth"]), queue_size)
    state["backpressure_events"] += 1
    state["last_backpressure_at"] = datetime.now(timezone.utc).isoformat()


def _optional_current_user() -> Any | None:
    return None


class IngestEventRequest(BaseModel):
    source_system: str
    entity_type: str
    source_event_id: Optional[str] = None
    payload: dict[str, Any]
    mapping_config_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_shape(cls, data: Any) -> Any:
        """Backward-compat: aceita payload "flat" (GAP-7) e converte para envelope canônico.

        Alguns testes/integrações antigas enviam:
          {player_id, event_type, amount, currency, metadata}
        Em vez de:
          {source_system, entity_type, payload, ...}
        """
        if not isinstance(data, dict):
            return data

        if {"source_system", "entity_type", "payload"}.issubset(data.keys()):
            return data

        legacy_keys = {"player_id", "event_type", "amount", "currency"}
        if not legacy_keys.issubset(data.keys()):
            return data

        occurred_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload: dict[str, Any] = {
            "player_id": data.get("player_id"),
            "amount": data.get("amount"),
            "currency": data.get("currency"),
            "transaction_type": data.get("event_type") or "DEPOSIT",
            "occurred_at": data.get("occurred_at") or occurred_at,
            "method": data.get("method") or "PIX",
            "status": data.get("status") or "SETTLED",
        }
        metadata = data.get("metadata")
        if isinstance(metadata, dict) and metadata:
            payload["metadata"] = metadata

        return {
            "source_system": data.get("source_system") or "BackofficeAlpha",
            "entity_type": data.get("entity_type") or "transaction",
            "source_event_id": data.get("source_event_id"),
            "mapping_config_id": data.get("mapping_config_id"),
            "payload": payload,
        }


class ReprocessRequest(BaseModel):
    mapping_version_id: Optional[str] = None
    reason: str = "manual_reprocess"


class WebsocketIngestRequest(BaseModel):
    source_system: str
    entity_type: str
    payload: dict[str, Any]
    source_event_id: Optional[str] = None
    mapping_config_id: Optional[str] = None


class ResolveIngestErrorRequest(BaseModel):
    note: Optional[str] = None


class ReplayIngestErrorRequest(BaseModel):
    corrected_payload: dict[str, Any]
    entity_type: Optional[str] = None
    mapping_config_id: Optional[str] = None
    apply_mapping: bool = True
    resolve_original: bool = True
    note: Optional[str] = None


class ConnectorParseSummary(BaseModel):
    accepted: int
    failed: int
    total: int
    errors: list[dict[str, Any]]


def _kafka_headers_from_context() -> list[tuple[str, bytes]] | None:
    """Build Kafka headers for request correlation (best-effort).

    If RequestIDMiddleware ran, `structlog.contextvars` contains `request_id`.
    """
    try:
        ctx = structlog.contextvars.get_contextvars()
        request_id = ctx.get("request_id")
        if isinstance(request_id, str) and request_id:
            return [("X-Request-ID", request_id.encode("utf-8"))]
    except Exception:
        return None
    return None


async def _publish_with_retries(
    *,
    producer: Any,
    topic: str,
    payload: dict[str, Any],
    key: str,
    tenant_id: str,
    source_system: str,
    context: dict[str, Any] | None = None,
    headers: list[tuple[str, bytes]] | None = None,
) -> bool:
    max_retries = int(getattr(settings, "dlq_max_retries", 3) or 3)
    effective_headers = headers if headers is not None else _kafka_headers_from_context()

    async def _send_best_effort(_topic: str, _payload: dict[str, Any]) -> None:
        try:
            await producer.send(_topic, _payload, key=key, headers=effective_headers)
        except TypeError as exc:
            # Alguns producers/fakes não aceitam `headers`.
            if "headers" in str(exc) and "unexpected keyword" in str(exc):
                await producer.send(_topic, _payload, key=key)
                return
            raise

    for attempt in range(1, max_retries + 1):
        try:
            await _send_best_effort(topic, payload)
            return True
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                try:
                    await _send_best_effort(
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
                    )
                except Exception as dlq_exc:  # noqa: BLE001
                    logger.error("ingest_dlq_publish_failed", error=str(dlq_exc), topic=topic)
                return False
            await asyncio.sleep(0.1 * attempt)
    return False


def _get_minio_client() -> Any | None:
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
    try:
        scoring_stmt = select(ScoringConfig.ingest_rate_limit_tpm).where(
            ScoringConfig.tenant_id == tenant_id
        )
        scoring_limit = (await db.execute(scoring_stmt)).scalar_one_or_none()
        if scoring_limit is not None:
            return max(1, int(cast(Any, scoring_limit)))
    except Exception:
        pass

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
            return max(1, int(cast(Any, row.flag_value)))

        if all(hasattr(SystemFlag, attr) for attr in ("key", "value")):
            tenant_scoped_key = f"{tenant_id}:ingest_rate_limit_per_min"
            stmt = select(SystemFlag).where(SystemFlag.key == tenant_scoped_key)
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                return max(1, int(cast(Any, row.value)))

            # Backward compatibility with old global key (non-tenant scoped)
            global_stmt = select(SystemFlag).where(SystemFlag.key == "ingest_rate_limit_per_min")
            global_row = (await db.execute(global_stmt)).scalar_one_or_none()
            if global_row:
                return max(1, int(cast(Any, global_row.value)))
            return default_limit
    except Exception:
        return default_limit

    return default_limit


async def _ensure_db_tenant_context(db: AsyncSession, tenant_id: str) -> None:
    """Guarantee Postgres RLS tenant context for ingest paths that may use API keys."""
    stmt = text("SELECT set_config('app.current_tenant', :tid, false)").bindparams(tid=str(tenant_id))
    await db.execute(stmt)


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
    ctx = structlog.contextvars.get_contextvars()
    request_id = ctx.get("request_id")
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
            **({"request_id": request_id} if request_id else {}),
            **(ingest_metadata or {}),
        },
    }


async def _resolve_effective_mapping_config(
    db: AsyncSession,
    *,
    tenant_id: str,
    source_system: str,
    entity_type: str,
    mapping_config_id: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    normalized_entity = entity_type.upper()

    if mapping_config_id:
        mapping_row = await db.get(MappingConfig, mapping_config_id)
        if not mapping_row or mapping_row.tenant_id != tenant_id:
            raise HTTPException(404, "MappingConfig não encontrado para o tenant")
        cfg = mapping_row.config_json if isinstance(mapping_row.config_json, dict) else None
        return str(mapping_row.id), cfg

    stmt = (
        select(MappingConfig)
        .where(
            MappingConfig.tenant_id == tenant_id,
            MappingConfig.source_system == source_system,
            MappingConfig.entity_type == normalized_entity,
            MappingConfig.is_current.is_(True),
            MappingConfig.active.is_(True),
        )
        .order_by(desc(MappingConfig.version_number))
        .limit(1)
    )
    mapping_row = (await db.execute(stmt)).scalar_one_or_none()
    if asyncio.iscoroutine(mapping_row):
        mapping_row = await mapping_row
    if mapping_row:
        cfg = mapping_row.config_json if isinstance(mapping_row.config_json, dict) else None
        return str(mapping_row.id), cfg

    default_cfg = get_default_mapping(source_system, normalized_entity)
    return None, dict(default_cfg) if isinstance(default_cfg, dict) else None


def _apply_mapping_config(config_json: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    if not config_json:
        return payload
    engine = MappingEngine(config_json)
    return engine.apply(payload)


async def _build_ingest_stream_snapshot(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)

    active_jobs = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IngestJob)
                .where(
                    IngestJob.tenant_id == tenant_id,
                    IngestJob.status.in_(("QUEUED", "PROCESSING")),
                )
            )
        ).scalar_one()
        or 0
    )
    failed_jobs_24h = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IngestJob)
                .where(
                    IngestJob.tenant_id == tenant_id,
                    IngestJob.created_at >= since_24h,
                    IngestJob.status.in_(("FAILED", "PARTIAL")),
                )
            )
        ).scalar_one()
        or 0
    )
    unresolved_errors = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IngestError)
                .where(
                    IngestError.tenant_id == tenant_id,
                    IngestError.resolved.is_(False),
                )
            )
        ).scalar_one()
        or 0
    )
    quarantine_breakdown_rows = (
        await db.execute(
            select(
                IngestError.source_system,
                IngestError.entity_type,
                func.count().label("error_count"),
            )
            .where(
                IngestError.tenant_id == tenant_id,
                IngestError.resolved.is_(False),
            )
            .group_by(IngestError.source_system, IngestError.entity_type)
            .order_by(desc(func.count()))
            .limit(5)
        )
    ).all()
    latest_job = (
        await db.execute(
            select(IngestJob)
            .where(IngestJob.tenant_id == tenant_id)
            .order_by(desc(IngestJob.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    recent_failed_jobs_stmt = (
        select(IngestJob)
        .where(
            IngestJob.tenant_id == tenant_id,
            IngestJob.status.in_(("FAILED", "PARTIAL")),
        )
        .order_by(desc(IngestJob.updated_at), desc(IngestJob.created_at))
        .limit(3)
    )
    recent_failed_jobs = (await db.execute(recent_failed_jobs_stmt)).scalars().all()
    configured_rate_limit = await _tenant_ingest_rate_limit(db, tenant_id, default_limit=300)
    ws_runtime = _ensure_ws_runtime(tenant_id)

    return {
        "active_jobs": active_jobs,
        "failed_jobs_24h": failed_jobs_24h,
        "unresolved_errors": unresolved_errors,
        "quarantine_breakdown": [
            {
                "source_system": str(source_system),
                "entity_type": str(entity_type) if entity_type is not None else None,
                "count": int(error_count or 0),
            }
            for source_system, entity_type, error_count in quarantine_breakdown_rows
        ],
        "configured_rate_limit_per_min": configured_rate_limit,
        "ws_active_connections": int(ws_runtime["active_connections"]),
        "ws_queued_messages": int(ws_runtime["queued_messages"]),
        "ws_peak_queue_depth": int(ws_runtime["peak_queue_depth"]),
        "ws_backpressure_events": int(ws_runtime["backpressure_events"]),
        "ws_max_queue_size": int(ws_runtime["max_queue_size"]),
        "ws_last_backpressure_at": ws_runtime["last_backpressure_at"],
        "latest_job_id": str(latest_job.id) if latest_job else None,
        "latest_job_status": latest_job.status if latest_job else None,
        "latest_source_system": latest_job.source_system if latest_job else None,
        "latest_job_updated_at": latest_job.updated_at.isoformat() if latest_job and latest_job.updated_at else None,
        "recent_failed_jobs": [
            {
                "id": str(job.id),
                "source_system": job.source_system,
                "status": job.status,
                "failed_records": job.failed_records,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
            for job in recent_failed_jobs
        ],
    }


@router.post("/ingest/event", status_code=202)
async def ingest_event(
    body: IngestEventRequest,
    principal: IngestPrincipal = Depends(get_ingest_principal),
    current_user: Any | None = Depends(_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not isinstance(principal, IngestPrincipal):
        if current_user is None:
            raise HTTPException(401, "Authentication required")
        principal = IngestPrincipal(
            tenant_id=current_user.tenant_id,
            id=current_user.id,
            role=current_user.role,
        )
    if body.source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{body.source_system}' não reconhecido. Permitidos: {sorted(ALLOWED_SOURCE_SYSTEMS)}")

    await _ensure_db_tenant_context(db, principal.tenant_id)
    max_requests = await _tenant_ingest_rate_limit(db, principal.tenant_id, default_limit=300)
    await redis_rate_limit(principal.tenant_id, "ingest.event", max_requests=max_requests)

    mapped_payload = body.payload
    effective_mapping_id = body.mapping_config_id
    if body.mapping_config_id:
        effective_mapping_id, mapping_cfg = await _resolve_effective_mapping_config(
            db,
            tenant_id=principal.tenant_id,
            source_system=body.source_system,
            entity_type=body.entity_type,
            mapping_config_id=body.mapping_config_id,
        )
        try:
            mapped_payload = _apply_mapping_config(mapping_cfg, body.payload)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Falha ao aplicar mapping no evento: {exc}") from exc

    # Rejeição de CARD_CREDIT para depósitos — Portaria SPA/MF 1.143/2024 art. 5º
    if body.entity_type == "TRANSACTION" and mapped_payload.get("method") == "CARD_CREDIT":
        db.add(IngestError(
            tenant_id=principal.tenant_id,
            source_system=body.source_system,
            entity_type="TRANSACTION",
            raw_payload=json.dumps(body.payload, ensure_ascii=False, default=str),
            error_reason="PAYMENT_METHOD_NOT_ALLOWED",
            error_detail={
                "method": "CARD_CREDIT",
                "rule": "Portaria SPA/MF 1.143/2024 art. 5 — cartão de crédito proibido para depósito em apostas reguladas",
            },
            resolved=False,
        ))
        await db.commit()
        return {"event_id": str(uuid.uuid4()), "status": "quarantined", "reason": "PAYMENT_METHOD_NOT_ALLOWED"}

    source_event_id = str(
        mapped_payload.get("event_id")
        or body.source_event_id
        or body.payload.get("event_id")
        or uuid.uuid4()
    )
    envelope = _build_envelope(
        tenant_id=principal.tenant_id,
        source_system=body.source_system,
        entity_type=body.entity_type,
        payload=mapped_payload,
        source_event_id=source_event_id,
        mapping_config_id=effective_mapping_id,
    )
    envelope["raw_payload"] = body.payload
    producer = await get_producer()
    if producer:
        topic = f"raw.{body.entity_type.lower()}s"
        ok = await _publish_with_retries(
            producer=producer,
            topic=topic,
            payload=envelope,
            key=source_event_id,
            tenant_id=principal.tenant_id,
            source_system=body.source_system,
            context={"endpoint": "/ingest/event"},
        )
        if not ok:
            raise HTTPException(503, "Falha ao enfileirar evento após retries; enviado para DLQ")
    return {"event_id": envelope["event_id"], "status": "queued"}


@router.post("/ingest/batch", status_code=202)
async def ingest_batch(
    events: list[IngestEventRequest],
    principal: IngestPrincipal = Depends(get_ingest_principal),
    current_user: Any | None = Depends(_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    # require_roles("ADMIN", "AML_ANALYST") is enforced for JWT flows in get_ingest_principal.
    if not isinstance(principal, IngestPrincipal):
        if current_user is None:
            raise HTTPException(401, "Authentication required")
        principal = IngestPrincipal(
            tenant_id=current_user.tenant_id,
            id=current_user.id,
            role=current_user.role,
        )
    await _ensure_db_tenant_context(db, principal.tenant_id)
    max_requests = await _tenant_ingest_rate_limit(db, principal.tenant_id, default_limit=50)
    await redis_rate_limit(principal.tenant_id, "ingest.batch", max_requests=max_requests)

    producer = await get_producer()
    results = []
    for body in events:
        if body.source_system not in ALLOWED_SOURCE_SYSTEMS:
            results.append({
                "status": "rejected",
                "reason": f"source_system inválido: {body.source_system}",
            })
            continue

        mapped_payload = body.payload
        effective_mapping_id = body.mapping_config_id
        if body.mapping_config_id:
            effective_mapping_id, mapping_cfg = await _resolve_effective_mapping_config(
                db,
                tenant_id=principal.tenant_id,
                source_system=body.source_system,
                entity_type=body.entity_type,
                mapping_config_id=body.mapping_config_id,
            )
            try:
                mapped_payload = _apply_mapping_config(mapping_cfg, body.payload)
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "status": "rejected",
                    "reason": f"mapping inválido: {exc}",
                })
                continue

        # Rejeição de CARD_CREDIT para depósitos — Portaria SPA/MF 1.143/2024 art. 5º
        if body.entity_type == "TRANSACTION" and mapped_payload.get("method") == "CARD_CREDIT":
            db.add(IngestError(
                tenant_id=principal.tenant_id,
                source_system=body.source_system,
                entity_type="TRANSACTION",
                raw_payload=json.dumps(body.payload, ensure_ascii=False, default=str),
                error_reason="PAYMENT_METHOD_NOT_ALLOWED",
                error_detail={
                    "method": "CARD_CREDIT",
                    "rule": "Portaria SPA/MF 1.143/2024 art. 5 — cartão de crédito proibido para depósito em apostas reguladas",
                },
                resolved=False,
            ))
            results.append({"status": "quarantined", "reason": "PAYMENT_METHOD_NOT_ALLOWED"})
            continue

        source_event_id = str(
            mapped_payload.get("event_id")
            or body.source_event_id
            or body.payload.get("event_id")
            or uuid.uuid4()
        )
        envelope = _build_envelope(
            tenant_id=principal.tenant_id,
            source_system=body.source_system,
            entity_type=body.entity_type,
            payload=mapped_payload,
            source_event_id=source_event_id,
            mapping_config_id=effective_mapping_id,
        )
        envelope["raw_payload"] = body.payload
        if producer:
            topic = f"raw.{body.entity_type.lower()}s"
            ok = await _publish_with_retries(
                producer=producer,
                topic=topic,
                payload=envelope,
                key=source_event_id,
                tenant_id=principal.tenant_id,
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
    principal: IngestPrincipal = Depends(get_ingest_principal),
    db: AsyncSession = Depends(get_db),
):
    _ = background_tasks
    await _ensure_db_tenant_context(db, principal.tenant_id)
    max_requests = await _tenant_ingest_rate_limit(db, principal.tenant_id, default_limit=20)
    await redis_rate_limit(principal.tenant_id, "ingest.file", max_requests=max_requests)

    content = await file.read()

    if source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{source_system}' não reconhecido. Permitidos: {sorted(ALLOWED_SOURCE_SYSTEMS)}")

    lines = [ln for ln in content.decode(errors="replace").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise HTTPException(400, "CSV inválido: arquivo deve conter ao menos uma linha de dados além do cabeçalho")

    effective_mapping_id, _ = await _resolve_effective_mapping_config(
        db,
        tenant_id=principal.tenant_id,
        source_system=source_system,
        entity_type="TRANSACTION",
        mapping_config_id=mapping_config_id,
    )
    mapping_version_id = effective_mapping_id

    job = IngestJob(
        tenant_id=principal.tenant_id,
        source_system=source_system,
        mapping_config_id=effective_mapping_id,
        mapping_version_id=mapping_version_id,
        file_name=file.filename,
        file_size_bytes=len(content),
        file_path=None,
        bytes_processed=0,
        status="QUEUED",
        created_by=principal.id,
    )
    db.add(job)
    await db.flush()
    job_pk = str(job.id)
    await db.commit()
    try:
        await db.refresh(job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_file_refresh_failed", job_id=job_pk, error=str(exc))
    else:
        if job.id:
            job_pk = str(job.id)

    bronze_path = _upload_bronze_file(
        tenant_id=principal.tenant_id,
        job_id=job_pk,
        file_name=file.filename or "ingest.csv",
        content=content,
    )
    if bronze_path:
        await _ensure_db_tenant_context(db, principal.tenant_id)
        job.file_path = bronze_path
        await db.commit()

    producer = await get_producer()
    if producer:
        msg = {
            "job_id": job_pk,
            "tenant_id": principal.tenant_id,
            "source_system": source_system,
            "mapping_config_id": effective_mapping_id,
            "mapping_version_id": mapping_version_id,
            "file_name": file.filename,
            "file_path": job.file_path,
        }
        ok = await _publish_with_retries(
            producer=producer,
            topic="ingest.jobs",
            payload=msg,
            key=job_pk,
            tenant_id=principal.tenant_id,
            source_system=source_system,
            context={"endpoint": "/ingest/file", "job_id": job_pk},
        )
        if not ok:
            raise HTTPException(503, "Falha ao enfileirar job de ingestão após retries; enviado para DLQ")

    return {
        "job_id": job_pk,
        "status": "QUEUED",
        "file_name": file.filename,
        "mapping_config_id": effective_mapping_id,
        "mapping_version_id": mapping_version_id,
    }


@router.post("/ingest/webhook/epsilon", status_code=202)
async def ingest_epsilon_webhook(
    request: Request,
    principal: IngestPrincipal = Depends(get_ingest_principal),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_db_tenant_context(db, principal.tenant_id)
    max_requests = await _tenant_ingest_rate_limit(db, principal.tenant_id, default_limit=300)
    await redis_rate_limit(principal.tenant_id, "ingest.webhook.epsilon", max_requests=max_requests)

    body = await request.body()
    mapping_config_id, mapping_cfg = await _resolve_effective_mapping_config(
        db,
        tenant_id=principal.tenant_id,
        source_system="ConnectorEpsilon",
        entity_type="TRANSACTION",
    )
    started_at = datetime.now(timezone.utc)
    job_file_name = f"epsilon-webhook-{started_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    job = IngestJob(
        tenant_id=principal.tenant_id,
        source_system="ConnectorEpsilon",
        mapping_config_id=mapping_config_id,
        mapping_version_id=mapping_config_id,
        connector_type="WEBHOOK",
        file_name=job_file_name,
        file_size_bytes=len(body),
        bytes_processed=0,
        status="PROCESSING",
        created_by=principal.id,
    )
    db.add(job)
    await db.flush()
    job_pk = str(job.id)
    await db.commit()
    try:
        await db.refresh(job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ingest_epsilon_refresh_failed", job_id=job_pk, error=str(exc))
    else:
        if job.id:
            job_pk = str(job.id)

    bronze_path = _upload_bronze_file(
        tenant_id=principal.tenant_id,
        job_id=job_pk,
        file_name=job_file_name,
        content=body,
    )
    if bronze_path:
        await _ensure_db_tenant_context(db, principal.tenant_id)
        job.file_path = bronze_path
        await db.commit()

    connector = ConnectorEpsilon(signing_secret=settings.epsilon_webhook_secret)
    result = connector.parse(
        body,
        headers={
            EPSILON_SIGNATURE_HEADER: request.headers.get(EPSILON_SIGNATURE_HEADER, ""),
            EPSILON_TIMESTAMP_HEADER: request.headers.get(EPSILON_TIMESTAMP_HEADER, ""),
        },
    )
    if not result.success:
        error_rows: list[dict[str, Any]] = []
        for err in result.errors:
            reason = err.get("reason", "webhook_validation_error") if isinstance(err, dict) else str(err)
            raw_payload = err.get("raw", body.decode("utf-8", errors="replace")[:300]) if isinstance(err, dict) else body.decode("utf-8", errors="replace")[:300]
            line_number = err.get("line") if isinstance(err, dict) else None
            error_rows.append({"line": line_number, "reason": reason, "raw": raw_payload})
            db.add(
                    IngestError(
                        tenant_id=principal.tenant_id,
                        ingest_job_id=job_pk,
                        source_system="ConnectorEpsilon",
                        entity_type="TRANSACTION",
                        raw_payload=str(raw_payload),
                    error_reason=reason,
                    error_detail={"channel": "webhook", "connector": "epsilon", "line": line_number},
                    line_number=line_number,
                    resolved=False,
                )
            )
        job.total_records = result.total
        job.processed_records = 0
        job.failed_records = result.failed
        job.bytes_processed = len(body)
        job.error_sample = error_rows[:10]
        job.status = "FAILED"
        job.duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        job.error_message = "webhook_validation_failed"
        await _ensure_db_tenant_context(db, principal.tenant_id)
        await db.commit()
        raise HTTPException(400, f"Webhook inválido: {result.errors}")

    producer = await get_producer()
    queued = 0
    failed = 0
    error_rows: list[dict[str, Any]] = []
    for rec in result.records:
        try:
            mapped_rec = _apply_mapping_config(mapping_cfg, rec)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            reason = f"mapping_failed: {exc}"
            error_rows.append({"line": None, "reason": reason, "raw": rec})
            db.add(
                IngestError(
                    tenant_id=principal.tenant_id,
                    ingest_job_id=job_pk,
                    source_system="ConnectorEpsilon",
                    entity_type="TRANSACTION",
                    raw_payload=json.dumps(rec, ensure_ascii=False),
                    error_reason=reason,
                    error_detail={
                        "channel": "webhook",
                        "connector": "epsilon",
                        "stage": "mapping",
                        "mapping_config_id": mapping_config_id,
                    },
                    resolved=False,
                )
            )
            continue

        source_event_id = mapped_rec.get("event_id") or rec.get("event_id") or str(uuid.uuid4())
        envelope = _build_envelope(
            tenant_id=principal.tenant_id,
            source_system="ConnectorEpsilon",
            entity_type="TRANSACTION",
            payload=mapped_rec,
            source_event_id=source_event_id,
            mapping_config_id=mapping_config_id,
            ingest_metadata={"channel": "webhook", "webhook": "epsilon", "job_id": job_pk},
        )
        envelope["raw_payload"] = rec
        if producer:
            ok = await _publish_with_retries(
                producer=producer,
                topic="raw.transactions",
                payload=envelope,
                key=source_event_id,
                tenant_id=principal.tenant_id,
                source_system="ConnectorEpsilon",
                context={"endpoint": "/ingest/webhook/epsilon"},
            )
            if not ok:
                failed += 1
                error_rows.append({"line": None, "reason": "publish_failed_after_retries", "raw": rec})
                db.add(
                    IngestError(
                        tenant_id=principal.tenant_id,
                        ingest_job_id=job_pk,
                        source_system="ConnectorEpsilon",
                        entity_type="TRANSACTION",
                        raw_payload=json.dumps(rec, ensure_ascii=False),
                        error_reason="publish_failed_after_retries",
                        error_detail={"channel": "webhook", "connector": "epsilon"},
                        resolved=False,
                    )
                )
                continue
        queued += 1
    job.total_records = result.total
    job.processed_records = queued
    job.failed_records = failed
    job.bytes_processed = len(body)
    job.error_sample = error_rows[:10]
    job.duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    job.status = "DONE" if failed == 0 else ("PARTIAL" if queued > 0 else "FAILED")
    await _ensure_db_tenant_context(db, principal.tenant_id)
    await db.commit()

    return {
        "status": "accepted",
        "count": queued,
        "job_id": job_pk,
        "mapping_config_id": mapping_config_id,
        "mapping_version_id": mapping_config_id,
    }


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
            "connector_type": j.connector_type,
            "status": j.status,
            "total_records": j.total_records,
            "processed_records": j.processed_records,
            "failed_records": j.failed_records,
            "bytes_processed": j.bytes_processed,
            "duration_ms": j.duration_ms,
            "mapping_config_id": j.mapping_config_id,
            "mapping_version_id": j.mapping_version_id,
            "error_message": j.error_message,
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
        "connector_type": j.connector_type,
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
        "mapping_config_id": j.mapping_config_id,
        "file_size_bytes": j.file_size_bytes,
        "bytes_processed": j.bytes_processed,
        "duration_ms": j.duration_ms,
        "reprocessed_from": j.reprocessed_from,
        "mapping_version_id": j.mapping_version_id,
        "file_path": j.file_path,
        "error_message": j.error_message,
        "error_sample_preview": j.error_sample or [],
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

    entity_type = (body.entity_type or err.entity_type or "TRANSACTION").upper()
    effective_mapping_id: str | None = None
    mapped_payload = dict(body.corrected_payload)
    if body.apply_mapping:
        effective_mapping_id, mapping_cfg = await _resolve_effective_mapping_config(
            db,
            tenant_id=str(current_user.tenant_id),
            source_system=str(err.source_system),
            entity_type=entity_type,
            mapping_config_id=body.mapping_config_id,
        )
        try:
            mapped_payload = _apply_mapping_config(mapping_cfg, body.corrected_payload)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Falha ao aplicar mapping no replay manual: {exc}") from exc
    elif body.mapping_config_id:
        mc = await db.get(MappingConfig, body.mapping_config_id)
        if not mc or mc.tenant_id != current_user.tenant_id:
            raise HTTPException(404, "MappingConfig não encontrado para o tenant")
        effective_mapping_id = str(mc.id)

    source_event_id = str(
        mapped_payload.get("event_id")
        or body.corrected_payload.get("event_id")
        or uuid.uuid4()
    )

    producer = await get_producer()
    if not producer:
        raise HTTPException(503, "Kafka indisponível para replay do erro")

    envelope = _build_envelope(
        tenant_id=str(current_user.tenant_id),
        source_system=str(err.source_system),
        entity_type=entity_type,
        payload=mapped_payload,
        source_event_id=source_event_id,
        mapping_config_id=effective_mapping_id,
        ingest_metadata={
            "channel": "quarantine_replay",
            "ingest_error_id": err.id,
            "replayed_by": current_user.id,
            "original_line_number": err.line_number,
        },
    )
    envelope["raw_payload"] = body.corrected_payload
    topic = f"raw.{entity_type.lower()}s"
    ok = await _publish_with_retries(
        producer=producer,
        topic=topic,
        payload=envelope,
        key=source_event_id,
        tenant_id=str(current_user.tenant_id),
        source_system=str(err.source_system),
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
            "mapping_config_id": effective_mapping_id,
            "apply_mapping": body.apply_mapping,
            "status": "QUEUED",
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
        "mapping_config_id": effective_mapping_id,
        "mapping_applied": body.apply_mapping,
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
    if not mapping_version_id:
        mapping_version_id, _ = await _resolve_effective_mapping_config(
            db,
            tenant_id=current_user.tenant_id,
            source_system=job.source_system,
            entity_type="TRANSACTION",
        )
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
        mapping_config_id=job.mapping_config_id or mapping_version_id,
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
        topic="ingest.jobs.reprocess",
        payload=msg,
        key=str(new_job.id),
        tenant_id=str(current_user.tenant_id),
        source_system=str(new_job.source_system),
        context={"endpoint": "/ingest/jobs/{job_id}/reprocess", "job_id": str(new_job.id)},
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

    queue_maxsize = 500
    queue: asyncio.Queue[WebsocketIngestRequest] = asyncio.Queue(maxsize=queue_maxsize)
    _ws_runtime_connected(tenant_id, max_queue_size=queue_maxsize)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                mapped_payload = item.payload
                effective_mapping_id = item.mapping_config_id
                if item.mapping_config_id:
                    async with AsyncSessionLocal() as mapping_db:
                        effective_mapping_id, mapping_cfg = await _resolve_effective_mapping_config(
                            mapping_db,
                            tenant_id=tenant_id,
                            source_system=item.source_system,
                            entity_type=item.entity_type,
                            mapping_config_id=item.mapping_config_id,
                        )
                    mapped_payload = _apply_mapping_config(mapping_cfg, item.payload)

                source_event_id = str(
                    mapped_payload.get("event_id")
                    or item.source_event_id
                    or item.payload.get("event_id")
                    or uuid.uuid4()
                )
                envelope = _build_envelope(
                    tenant_id=tenant_id,
                    source_system=item.source_system,
                    entity_type=item.entity_type,
                    payload=mapped_payload,
                    source_event_id=source_event_id,
                    mapping_config_id=effective_mapping_id,
                    ingest_metadata={"channel": "websocket"},
                )
                envelope["raw_payload"] = item.payload
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
                _ws_runtime_acked(tenant_id, queue_size=queue.qsize())
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
                _ws_runtime_enqueued(tenant_id, queue_size=queue.qsize())
            except asyncio.QueueFull:
                _ws_runtime_backpressure(tenant_id, queue_size=queue.qsize())
                await websocket.send_json({
                    "status": "backpressure",
                    "detail": "fila temporariamente cheia",
                    "queued": queue.qsize(),
                    "retry_ms": 500,
                })
    except WebSocketDisconnect:
        logger.info("ingest_ws_disconnected", tenant_id=tenant_id)
    finally:
        _ws_runtime_disconnected(tenant_id)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


@router.post("/ingest/connectors/{connector_name}/parse", status_code=202)
async def parse_connector_payload(
    connector_name: str,
    file: UploadFile = File(...),
    entity_type: str = Form("TRANSACTION"),
    mapping_config_id: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    """Dedicated parser endpoint for Gamma/XML and Delta/NDJSON connectors.

    Validates connector payload, quarantines parse errors and enqueues valid events.
    """
    name = connector_name.strip().lower()
    if name not in {"gamma", "delta"}:
        raise HTTPException(400, "connector_name deve ser 'gamma' ou 'delta' para este endpoint")

    if not isinstance(mapping_config_id, str):
        mapping_config_id = None

    await _ensure_db_tenant_context(db, str(current_user.tenant_id))
    max_requests = await _tenant_ingest_rate_limit(db, str(current_user.tenant_id), default_limit=120)
    await redis_rate_limit(str(current_user.tenant_id), f"ingest.connector.{name}", max_requests=max_requests)

    content = await file.read()
    source_system = "ConnectorGamma" if name == "gamma" else "ConnectorDelta"
    connector_kwargs = {"root_tag": "transaction"} if name == "gamma" else {}
    connector = get_connector(name, **connector_kwargs)
    parse_result = connector.parse(content, entity_type=entity_type)
    effective_mapping_id, mapping_cfg = await _resolve_effective_mapping_config(
        db,
        tenant_id=str(current_user.tenant_id),
        source_system=source_system,
        entity_type=entity_type,
        mapping_config_id=mapping_config_id,
    )

    # Register an ingest job for observability and reprocessing trail
    job = IngestJob(
        tenant_id=current_user.tenant_id,
        source_system=source_system,
        mapping_config_id=effective_mapping_id,
        mapping_version_id=effective_mapping_id,
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
    await db.flush()
    job_pk = str(job.id)
    await db.commit()
    try:
        await db.refresh(job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("parse_connector_refresh_failed", job_id=job_pk, error=str(exc))
    else:
        if job.id:
            job_pk = str(job.id)

    bronze_path = _upload_bronze_file(
        tenant_id=str(current_user.tenant_id),
        job_id=job_pk,
        file_name=file.filename or f"{name}.payload",
        content=content,
    )
    if bronze_path:
        await _ensure_db_tenant_context(db, str(current_user.tenant_id))
        job.file_path = bronze_path
        await db.commit()

    producer = await get_producer()
    accepted = 0
    failed = 0
    error_rows: list[dict[str, Any]] = []

    for rec in parse_result.records:
        try:
            mapped_rec = _apply_mapping_config(mapping_cfg, rec)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            reason = f"mapping_failed: {exc}"
            error_rows.append({"line": None, "reason": reason, "raw": rec})
            db.add(
                IngestError(
                    tenant_id=current_user.tenant_id,
                    ingest_job_id=job_pk,
                    source_system=source_system,
                    entity_type=entity_type,
                    raw_payload=json.dumps(rec, ensure_ascii=False),
                    error_reason=reason,
                    error_detail={"connector": name, "stage": "mapping", "mapping_config_id": effective_mapping_id},
                    resolved=False,
                )
            )
            continue

        source_event_id = str(mapped_rec.get("event_id") or rec.get("event_id") or uuid.uuid4())
        envelope = _build_envelope(
            tenant_id=str(current_user.tenant_id),
            source_system=source_system,
            entity_type=entity_type,
            payload=mapped_rec,
            source_event_id=source_event_id,
            mapping_config_id=effective_mapping_id,
            ingest_metadata={"channel": "connector-parse", "job_id": job_pk},
        )
        envelope["raw_payload"] = rec
        topic = f"raw.{entity_type.lower()}s"
        if producer:
            ok = await _publish_with_retries(
                producer=producer,
                topic=topic,
                payload=envelope,
                key=source_event_id,
                tenant_id=str(current_user.tenant_id),
                source_system=source_system,
                context={"endpoint": "/ingest/connectors/{connector_name}/parse", "job_id": job_pk},
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
                ingest_job_id=job_pk,
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
    await _ensure_db_tenant_context(db, str(current_user.tenant_id))
    await db.commit()

    return {
        "job_id": job_pk,
        "source_system": source_system,
        "mapping_config_id": effective_mapping_id,
        "mapping_version_id": effective_mapping_id,
        "status": job.status,
        "summary": ConnectorParseSummary(
            accepted=accepted,
            failed=failed,
            total=parse_result.total,
            errors=error_rows[:20],
        ).model_dump(),
    }


# ── SSE ingest stream ─────────────────────────────────────────────────────────

from auth import get_current_user  # noqa: E402  (avoid circular-import at module top)

UTC_TZ = timezone.utc


@router.get("/ingest/stream")
async def ingest_sse_stream(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Server-Sent Events — streams real-time ingest heartbeat / status updates.

    In production this would subscribe to a Redis pub/sub channel for live progress.
    Currently emits a heartbeat every 5 s and disconnects when the client closes.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        ping_count = 0
        while True:
            if await request.is_disconnected():
                break
            ping_count += 1
            snapshot = await _build_ingest_stream_snapshot(db, str(current_user.tenant_id))
            payload = json.dumps({
                "type": "ingest_snapshot",
                "count": ping_count,
                "ts": datetime.now(UTC_TZ).isoformat(),
                "summary": snapshot,
            })
            yield f"data: {payload}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
