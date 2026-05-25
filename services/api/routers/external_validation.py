"""external_validation.py — Endpoints para validação externa de identidade."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_roles
from database import AsyncSessionLocal, get_db
from metrics import observe_external_validation_request, observe_external_validation_result
from models import ExternalValidationRequest, Player, User
from utils import write_audit

router = APIRouter(tags=["external_validation"])
logger = structlog.get_logger(__name__)

class ExternalValidationRequestIn(BaseModel):
    provider: str | None = None
    validation_type: str = "CPF_IDENTITY"
    payload: dict = Field(default_factory=dict)


class ExternalValidationProviderStatusOut(BaseModel):
    configured_provider: str
    provider_url_configured: bool
    provider_token_configured: bool
    environment: str
    mock_allowed: bool
    timeout_seconds: float


IDEMPOTENCY_WINDOW_MINUTES = 10
MAX_PROVIDER_RETRIES = 3
_PROVIDER_CB_UNTIL: dict[str, datetime] = {}

_VALIDATION_PROVIDER = os.getenv("EXTERNAL_VALIDATION_PROVIDER", "mock_identity").strip().lower()
_VALIDATION_PROVIDER_URL = os.getenv("EXTERNAL_VALIDATION_PROVIDER_URL", "").strip()
_VALIDATION_PROVIDER_TOKEN = os.getenv("EXTERNAL_VALIDATION_PROVIDER_TOKEN", "").strip()
_BETAML_ENV = os.getenv("BETAML_ENVIRONMENT", os.getenv("ENVIRONMENT", "development")).strip().lower()


def _provider_timeout_seconds() -> float:
    raw = os.getenv("EXTERNAL_VALIDATION_PROVIDER_TIMEOUT_SECONDS", "5").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 5.0


def _is_mock_provider(provider: str) -> bool:
    return provider in {"mock", "mock_identity"}


def _resolve_effective_provider(requested_provider: str | None) -> str:
    configured_provider = _VALIDATION_PROVIDER or "mock_identity"
    requested = (requested_provider or "").strip().lower()

    if not requested or requested in {"default", "configured"}:
        return configured_provider

    if _is_mock_provider(configured_provider):
        if not _is_mock_provider(requested):
            raise HTTPException(
                400,
                "Provider solicitado não está habilitado neste ambiente. "
                "Configure EXTERNAL_VALIDATION_PROVIDER para usar um provider real.",
            )
        return "mock_identity"

    if _is_mock_provider(requested):
        raise HTTPException(
            400,
            "Provider mock_identity não é permitido quando um provider real está configurado.",
        )

    if requested != configured_provider:
        raise HTTPException(
            400,
            f"Provider solicitado '{requested}' diverge do provider ativo '{configured_provider}'.",
        )

    return configured_provider


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


@router.get("/external-validation/provider-contract", response_model=ExternalValidationProviderStatusOut)
async def get_external_validation_provider_contract(
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST", "AUDITOR")),
) -> ExternalValidationProviderStatusOut:
    """Expõe o contrato operacional do provider de validação externa.

    O endpoint existe para readiness/go-live: o ambiente deve usar provider real
    fora de development/test e deixar isso explícito para operação e auditoria.
    """
    configured_provider = _VALIDATION_PROVIDER or "mock_identity"
    return ExternalValidationProviderStatusOut(
        configured_provider=configured_provider,
        provider_url_configured=bool(_VALIDATION_PROVIDER_URL),
        provider_token_configured=bool(_VALIDATION_PROVIDER_TOKEN),
        environment=_BETAML_ENV,
        mock_allowed=_BETAML_ENV in {"development", "test"},
        timeout_seconds=_provider_timeout_seconds(),
    )


async def _set_tenant_context(db: AsyncSession, tenant_id: str) -> None:
    """Best-effort tenant context for RLS-aware background sessions."""
    try:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, false)"),
            {"tid": str(tenant_id)},
        )
    except Exception:
        return


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


async def _http_provider_call(provider: str, validation_type: str, request_id: str, payload: dict) -> dict:
    if not _VALIDATION_PROVIDER_URL:
        raise RuntimeError("external_validation_provider_url_missing")
    provider_url = urllib.parse.urlparse(_VALIDATION_PROVIDER_URL)
    if provider_url.scheme not in {"http", "https"} or not provider_url.netloc:
        raise RuntimeError("external_validation_provider_url_invalid")

    timeout_seconds = _provider_timeout_seconds()

    def _call_provider() -> dict:
        request_body = {
            "request_id": request_id,
            "provider": provider,
            "validation_type": validation_type,
            "payload": payload,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-BetAML-Request-ID": request_id,
        }
        if _VALIDATION_PROVIDER_TOKEN:
            headers["Authorization"] = f"Bearer {_VALIDATION_PROVIDER_TOKEN}"

        req = urllib.request.Request(
            url=_VALIDATION_PROVIDER_URL,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # nosec B310
            parsed = json.loads(resp.read().decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RuntimeError("provider_response_not_json_object")
            return parsed

    try:
        response = await asyncio.to_thread(_call_provider)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"provider_request_failed:{exc}") from exc

    return {
        "provider": provider,
        "validation_type": validation_type,
        "match": bool(response.get("match") or False),
        "match_score": float(response.get("match_score") or 0.0),
        "risk_hint": str(response.get("risk_hint") or "UNKNOWN"),
        "validated_at": str(response.get("validated_at") or datetime.now(timezone.utc).isoformat()),
        "external_request_id": str(response.get("external_request_id") or response.get("request_id") or request_id),
        "provider_payload": response,
    }


async def _dispatch_provider_call(provider: str, validation_type: str, request_id: str, payload: dict) -> dict:
    """Despacha para o provider configurado via EXTERNAL_VALIDATION_PROVIDER.

    Em qualquer ambiente fora de development/test, o mock é bloqueado para
    evitar validações externas silenciosamente falsas em staging/produção.
    """
    if _is_mock_provider(provider):
        if _BETAML_ENV not in ("development", "test"):
            logger.error(
                "external_validation_mock_blocked_outside_dev_test",
                provider=provider,
                validation_type=validation_type,
                request_id=request_id,
                environment=_BETAML_ENV,
                hint="Configure EXTERNAL_VALIDATION_PROVIDER com um provider real.",
            )
            raise RuntimeError("mock_provider_not_allowed_outside_dev_test")
        return await _mock_provider_call(provider, validation_type, request_id)

    return await _http_provider_call(provider, validation_type, request_id, payload)


async def _process_validation_request(request_id: str, tenant_id: str | None = None) -> None:
    """Processamento assíncrono para provider externo."""
    async with AsyncSessionLocal() as db:
        if tenant_id:
            await _set_tenant_context(db, tenant_id)
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
                    response = await _dispatch_provider_call(
                        req.provider,
                        req.validation_type,
                        request_id,
                        req.request_payload or {},
                    )
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

            # ── Verificação automática nas listas de sanções / PEP ────────────────
            # Executada após o provider responder, antes de persistir o resultado,
            # para que analistas já recebam o consolidado numa única leitura.
            try:
                from auth import decrypt_pii  # noqa: PLC0415
                from sanctions import get_sanctions_checker  # noqa: PLC0415

                player = await db.get(Player, req.player_id)
                if player:
                    player_cpf_hmac: str | None = getattr(player, "cpf_hmac", None)
                    player_name: str | None = None
                    sanctions_warning: str | None = None
                    try:
                        player_name = decrypt_pii(player.name_encrypted) if player.name_encrypted else None
                    except Exception as exc:  # pragma: no cover
                        sanctions_warning = "player_name_unavailable_due_to_pii_decryption_error"
                        logger.warning(
                            "external_validation_player_name_decrypt_failed",
                            request_id=request_id,
                            player_id=req.player_id,
                            tenant_id=req.tenant_id,
                            error=str(exc),
                        )

                    checker = get_sanctions_checker()
                    _sanctions_result = checker.check(cpf_hmac=player_cpf_hmac, name=player_name)
                    response["sanctions_check"] = _sanctions_result.to_dict()
                    if sanctions_warning:
                        response["sanctions_check"]["warning"] = sanctions_warning
            except Exception as _sanctions_exc:  # noqa: BLE001 — nunca bloqueia o resultado principal
                response["sanctions_check"] = {"error": str(_sanctions_exc), "matched": False}

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

    effective_provider = _resolve_effective_provider(body.provider)

    window_start = datetime.now(timezone.utc) - timedelta(minutes=IDEMPOTENCY_WINDOW_MINUTES)
    existing_q = (
        select(ExternalValidationRequest)
        .where(
            ExternalValidationRequest.tenant_id == current_user.tenant_id,
            ExternalValidationRequest.player_id == player_id,
            ExternalValidationRequest.provider == effective_provider,
            ExternalValidationRequest.validation_type == body.validation_type,
            ExternalValidationRequest.requested_at >= window_start,
            ExternalValidationRequest.status.in_(["PENDING", "IN_PROGRESS", "COMPLETED"]),
        )
        .order_by(ExternalValidationRequest.requested_at.desc())
        .limit(1)
    )
    existing = (await db.execute(existing_q)).scalars().first()
    if existing:
        if existing.status in {"PENDING", "IN_PROGRESS"}:
            background_tasks.add_task(
                _process_validation_request,
                str(existing.id),
                str(current_user.tenant_id),
            )
        payload = _serialize_validation(existing)
        payload["idempotent_reuse"] = True
        return payload

    # Cria requisição PENDING e processa em background (modelo assíncrono real).
    ext_req = ExternalValidationRequest(
        id=str(uuid.uuid4()),
        tenant_id=current_user.tenant_id,
        player_id=player_id,
        provider=effective_provider,
        validation_type=body.validation_type,
        status="PENDING",
        request_payload=body.payload,
        response_payload={},
        requested_by=current_user.id,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(ext_req)
    observe_external_validation_request(effective_provider, body.validation_type)
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "EXTERNAL_VALIDATION_REQUEST",
        "Player",
        player_id,
        after={
            **(body.payload or {}),
            "provider": effective_provider,
            "validation_type": body.validation_type,
        },
    )
    await db.commit()
    background_tasks.add_task(
        _process_validation_request,
        str(ext_req.id),
        str(current_user.tenant_id),
    )
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
    background_tasks.add_task(
        _process_validation_request,
        str(retry_req.id),
        str(current_user.tenant_id),
    )
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
