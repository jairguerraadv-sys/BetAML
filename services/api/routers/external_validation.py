"""external_validation.py — Endpoints para validação externa de identidade (mock/provider)."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from database import AsyncSessionLocal, get_db
from metrics import observe_external_validation_request, observe_external_validation_result
from models import ExternalValidationRequest, Player, User
from utils import write_audit

router = APIRouter(tags=["external_validation"])

class ExternalValidationRequestIn(BaseModel):
    provider: str = "mock_identity"
    validation_type: str = "CPF_IDENTITY"
    payload: dict = Field(default_factory=dict)


IDEMPOTENCY_WINDOW_MINUTES = 10
MAX_PROVIDER_RETRIES = 3
_PROVIDER_CB_UNTIL: dict[str, datetime] = {}


def _cb_window_seconds() -> int:
    try:
        return max(1, int(os.getenv("EXTERNAL_VALIDATION_CB_SECONDS", "30")))
    except ValueError:
        return 30


def _serialize_validation(req: ExternalValidationRequest) -> dict:
    return {
        "request_id": req.id,
        "status": req.status,
        "response": req.response_payload,
        "provider": req.provider,
        "validation_type": req.validation_type,
        "requested_at": req.requested_at,
        "completed_at": req.completed_at,
        "error_message": req.error_message,
    }


async def _mock_provider_call(provider: str, validation_type: str, request_id: str) -> dict:
    # Simula latência e resposta de provider externo.
    now = datetime.now(timezone.utc)
    cb_until = _PROVIDER_CB_UNTIL.get(provider)
    if cb_until and now < cb_until:
        raise RuntimeError(f"provider_circuit_open_until:{cb_until.isoformat()}")

    # Modo de falha forçada para testes de resiliência.
    if os.getenv("EXTERNAL_VALIDATION_FORCE_FAIL", "0") == "1":
        _PROVIDER_CB_UNTIL[provider] = now + timedelta(seconds=_cb_window_seconds())
        raise RuntimeError("provider_unavailable_forced")

    await asyncio.sleep(0.2)
    return {
        "provider": provider,
        "validation_type": validation_type,
        "match": True,
        "match_score": 0.97,
        "risk_hint": "LOW",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "external_request_id": f"mock-{request_id[:8]}",
    }


async def _process_validation_request(request_id: str) -> None:
    """Processamento assíncrono mockado para provider externo."""
    async with AsyncSessionLocal() as db:
        req = await db.get(ExternalValidationRequest, request_id)
        if not req:
            return

        req.status = "IN_PROGRESS"
        started_at = datetime.now(timezone.utc)
        await db.commit()

        try:
            last_exc: Exception | None = None
            response: dict | None = None
            for attempt in range(1, MAX_PROVIDER_RETRIES + 1):
                try:
                    response = await _mock_provider_call(req.provider, req.validation_type, request_id)
                    response["attempts"] = attempt
                    response["retries_count"] = max(0, attempt - 1)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt >= MAX_PROVIDER_RETRIES:
                        raise
                    await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

            if response is None:
                raise RuntimeError(str(last_exc or "provider_response_empty"))

            req.status = "COMPLETED"
            req.external_request_id = str(response.get("external_request_id") or f"mock-{request_id[:8]}")
            response["latency_ms"] = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            req.response_payload = response
            req.completed_at = datetime.now(timezone.utc)
            observe_external_validation_result(req.provider, "COMPLETED")
        except Exception as exc:  # noqa: BLE001
            req.status = "FAILED"
            req.error_message = str(exc)
            req.response_payload = {
                "provider": req.provider,
                "validation_type": req.validation_type,
                "error": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "retries_count": MAX_PROVIDER_RETRIES,
                "latency_ms": int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
            }
            req.completed_at = datetime.now(timezone.utc)
            observe_external_validation_result(req.provider, "FAILED")

        await db.commit()

@router.post("/players/{player_id}/external-validation", status_code=201)
async def request_external_validation(
    player_id: str,
    background_tasks: BackgroundTasks,
    body: ExternalValidationRequestIn,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    window_start = datetime.now(timezone.utc) - timedelta(minutes=IDEMPOTENCY_WINDOW_MINUTES)
    existing_q = (
        select(ExternalValidationRequest)
        .where(
            ExternalValidationRequest.tenant_id == current_user.tenant_id,
            ExternalValidationRequest.player_id == player_id,
            ExternalValidationRequest.provider == body.provider,
            ExternalValidationRequest.validation_type == body.validation_type,
            ExternalValidationRequest.requested_at >= window_start,
            ExternalValidationRequest.status.in_(["PENDING", "IN_PROGRESS", "COMPLETED"]),
        )
        .order_by(ExternalValidationRequest.requested_at.desc())
        .limit(1)
    )
    existing = (await db.execute(existing_q)).scalars().first()
    if existing:
        payload = _serialize_validation(existing)
        payload["idempotent_reuse"] = True
        return payload

    # Cria requisição PENDING e processa em background (modelo assíncrono real).
    ext_req = ExternalValidationRequest(
        id=str(uuid.uuid4()),
        tenant_id=current_user.tenant_id,
        player_id=player_id,
        provider=body.provider,
        validation_type=body.validation_type,
        status="PENDING",
        request_payload=body.payload,
        response_payload={},
        requested_by=current_user.id,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(ext_req)
    observe_external_validation_request(body.provider, body.validation_type)
    await write_audit(db, current_user.tenant_id, current_user.id, "EXTERNAL_VALIDATION_REQUEST", "Player", player_id, after=body.payload)
    await db.commit()
    background_tasks.add_task(_process_validation_request, str(ext_req.id))
    return _serialize_validation(ext_req)

@router.get("/players/{player_id}/external-validation/latest")
async def get_latest_external_validation(
    player_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    q = select(ExternalValidationRequest).where(
        ExternalValidationRequest.player_id == player_id,
        ExternalValidationRequest.tenant_id == current_user.tenant_id,
    ).order_by(ExternalValidationRequest.requested_at.desc())
    ext_req = (await db.execute(q)).scalars().first()
    if not ext_req:
        raise HTTPException(404, "Nenhuma validação externa encontrada para este jogador")
    return _serialize_validation(ext_req)


@router.get("/external-validation/{request_id}")
async def get_external_validation_by_id(
    request_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    req = await db.get(ExternalValidationRequest, request_id)
    if not req or str(req.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Validação externa não encontrada")
    return _serialize_validation(req)


@router.post("/external-validation/{request_id}/retry", status_code=202)
async def retry_external_validation(
    request_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    req = await db.get(ExternalValidationRequest, request_id)
    if not req or str(req.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Validação externa não encontrada")
    if req.status != "FAILED":
        raise HTTPException(400, "Apenas validações com status FAILED podem ser reprocessadas")

    retry_req = ExternalValidationRequest(
        id=str(uuid.uuid4()),
        tenant_id=req.tenant_id,
        player_id=req.player_id,
        provider=req.provider,
        validation_type=req.validation_type,
        status="PENDING",
        request_payload=req.request_payload or {},
        response_payload={},
        requested_by=current_user.id,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(retry_req)
    observe_external_validation_request(retry_req.provider, retry_req.validation_type)
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "EXTERNAL_VALIDATION_RETRY",
        "ExternalValidationRequest",
        request_id,
        after={"retry_request_id": str(retry_req.id)},
    )
    await db.commit()
    background_tasks.add_task(_process_validation_request, str(retry_req.id))
    return {
        "status": "QUEUED",
        "request_id": str(retry_req.id),
        "retries_from": request_id,
    }


@router.get("/players/{player_id}/external-validation/history")
async def list_external_validation_history(
    player_id: str,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    provider: str | None = Query(None),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    filters = [
        ExternalValidationRequest.player_id == player_id,
        ExternalValidationRequest.tenant_id == current_user.tenant_id,
    ]
    if status:
        filters.append(ExternalValidationRequest.status == status)
    if provider:
        filters.append(ExternalValidationRequest.provider == provider)

    q = (
        select(ExternalValidationRequest)
        .where(*filters)
        .order_by(ExternalValidationRequest.requested_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = (await db.execute(q)).scalars().all()

    total_q = select(func.count()).select_from(ExternalValidationRequest).where(*filters)
    total = int((await db.execute(total_q)).scalar_one() or 0)

    return {
        "player_id": player_id,
        "limit": limit,
        "offset": offset,
        "total": total,
        "items": [
            {
                **_serialize_validation(it),
            }
            for it in items
        ],
    }