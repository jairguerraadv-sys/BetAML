"""
utils.py — Utilitários compartilhados entre routers.

Expõe:
  - write_audit(db, tenant_id, user_id, action, entity_type, ...)
  - get_producer()   → KafkaProducerClient (lazy singleton)
  - redis_rate_limit(tenant_id, bucket, max_requests, window_seconds)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = structlog.get_logger(__name__)

_AUDIT_REDACTED_KEYS = {
    "access_token",
    "authorization",
    "cpf",
    "cpf_encrypted",
    "email",
    "full_name",
    "name",
    "password",
    "payload",
    "raw_payload",
    "refresh_token",
    "response_payload",
    "token",
}

_SENSITIVE_PAYLOAD_KEYS = {
    "cpf",
    "cpf_encrypted",
    "document",
    "document_number",
    "email",
    "full_name",
    "name",
    "phone",
    "raw_payload",
    "token",
}


def _sanitize_audit_payload(value: Any, *, depth: int = 0) -> Any:
    if value is None:
        return None
    if depth >= 4:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _AUDIT_REDACTED_KEYS:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_audit_payload(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_audit_payload(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, tuple):
        return tuple(_sanitize_audit_payload(item, depth=depth + 1) for item in value[:20])
    if isinstance(value, str) and len(value) > 1000:
        return value[:1000] + "...[TRUNCATED]"
    return value


def sanitize_sensitive_payload(value: Any, *, depth: int = 0) -> Any:
    """Sanitiza payloads potencialmente sensíveis para retorno em API/logs.

    Regras:
    - mascara chaves conhecidas de PII/token;
    - limita profundidade e cardinalidade para evitar vazamento massivo;
    - trunca strings longas.
    """
    if value is None:
        return None
    if depth >= 4:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_PAYLOAD_KEYS:
                out[key] = "[REDACTED]"
            else:
                out[key] = sanitize_sensitive_payload(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize_sensitive_payload(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, tuple):
        return tuple(sanitize_sensitive_payload(item, depth=depth + 1) for item in value[:20])
    if isinstance(value, str):
        if len(value) > 256:
            return value[:256] + "...[TRUNCATED]"
        return value
    return value

# ─── Kafka producer (lazy singleton) ──────────────────────────────────────────
_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        try:
            from libs.clients import KafkaProducerClient
            _producer = KafkaProducerClient(settings.kafka_bootstrap_servers)
            await _producer.start()
        except Exception as e:
            _producer = None  # reset so next call retries
            logger.warning("kafka_producer_unavailable", error=str(e))
    return _producer


async def stop_producer():
    global _producer
    if _producer:
        try:
            await _producer.stop()
        except Exception:
            pass
        _producer = None


# ─── AuditLog helper ──────────────────────────────────────────────────────────

async def write_audit(
    db: AsyncSession,
    tenant_id: Any,
    user_id: Any = None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    ip: Optional[str] = None,
    pii_accessed: Optional[str] = None,
    *,
    actor_id: Any = None,
) -> None:
    """
    Persiste um registro de AuditLog. Faz flush mas não commit.

    Args:
        pii_accessed: Se não None, registra acesso a PII específico (ex: "cpf", "full_name").
                     Exemplo: pii_accessed="cpf" registra "ACCESS_PII: cpf acessado".
                     Exigido para LGPD Art. 37 (auditoria de acesso a dados pessoais).
    """
    from models import AuditLog  # importação local para evitar circular

    if action is None or entity_type is None:
        raise ValueError("write_audit requer 'action' e 'entity_type'")

    # Backward-compat: alguns routers usam `actor_id=` (mesma coisa que `user_id`).
    if user_id is None and actor_id is not None:
        user_id = actor_id

    # RLS/tenant isolation: garante que a sessão do Postgres está com o tenant
    # correto antes do INSERT no audit_logs. Best-effort para não quebrar testes
    # unitários com SQLite/mocks.
    try:
        if tenant_id is not None:
            await db.execute(
                text("SELECT set_config('app.current_tenant', :tid, false)"),
                {"tid": str(tenant_id)},
            )
    except Exception:
        pass

    # Se PII foi acessado, registra explicitamente
    if pii_accessed:
        action = f"ACCESS_PII:{pii_accessed}"

    al = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=_sanitize_audit_payload(before),
        after=_sanitize_audit_payload(after),
        pii_accessed=pii_accessed,
        ip_address=ip,
    )
    db.add(al)
    await db.flush()


# ─── Redis rate limiter (sliding window — 1 minute) ───────────────────────────

_rate_redis: Any = None


async def _get_rate_redis():
    global _rate_redis
    if _rate_redis is None:
        try:
            import redis.asyncio as aioredis
            _rate_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await _rate_redis.ping()
        except Exception:
            _rate_redis = None
    return _rate_redis


async def redis_rate_limit(
    tenant_id: str,
    bucket: str,
    max_requests: int,
    window_seconds: int = 60,
) -> None:
    """
    Implementa sliding window rate limiting via Redis INCR+EXPIRE.
    Lança HTTPException 429 se o limite for excedido.
    Falha silenciosa se Redis não estiver disponível.
    """
    r = await _get_rate_redis()
    if r is None:
        return  # sem Redis → não bloquear por falta de infra

    minute_key = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    key = f"betaml:rate:{tenant_id}:{bucket}:{minute_key}"
    try:
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, window_seconds)
        if count > max_requests:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit excedido para '{bucket}': "
                    f"{max_requests} req/{window_seconds}s por tenant. "
                    "Aguarde e tente novamente."
                ),
                headers={"Retry-After": str(window_seconds)},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("rate_limit_redis_error", error=str(e))


# ── Multiplicadores por plano ──────────────────────────────────────────────────
_PLAN_TIER_MULTIPLIER: dict[str, float] = {
    "starter":      0.5,
    "standard":     1.0,
    "professional": 2.0,
    "enterprise":   5.0,
}


async def redis_rate_limit_by_plan(
    db: AsyncSession,
    tenant_id: str,
    bucket: str,
    base_max_requests: int,
    window_seconds: int = 60,
) -> None:
    """Rate limiting com multiplicadores por plano contratual do tenant.

    Consulta `tenants.plan_tier` (cached by Redis para evitar round-trip a cada request)
    e aplica o multiplicador sobre `base_max_requests`.

    Tiers suportados: starter (0.5×), standard (1×), professional (2×), enterprise (5×).
    """
    from sqlalchemy import select as _select
    from models import Tenant as _Tenant

    # Tentar obter plan_tier do Redis (cache curta — 5 min)
    r = await _get_rate_redis()
    plan_tier: str | None = None
    cache_key = f"betaml:plantier:{tenant_id}"
    if r:
        try:
            plan_tier = await r.get(cache_key)
        except Exception:
            pass

    if not plan_tier:
        try:
            result = await db.execute(
                _select(_Tenant.plan_tier).where(_Tenant.id == tenant_id)
            )
            plan_tier = result.scalar_one_or_none() or "standard"
            if r:
                try:
                    await r.set(cache_key, plan_tier, ex=300)
                except Exception:
                    pass
        except Exception:
            plan_tier = "standard"

    multiplier = _PLAN_TIER_MULTIPLIER.get(plan_tier, 1.0)
    effective_limit = max(1, int(base_max_requests * multiplier))
    await redis_rate_limit(tenant_id, bucket, effective_limit, window_seconds)
