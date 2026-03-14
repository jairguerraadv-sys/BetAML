"""
utils.py — Utilitários compartilhados entre routers.

Expõe:
  - write_audit(db, tenant_id, user_id, action, entity_type, ...)
  - get_producer()   → KafkaProducerClient (lazy singleton)
  - redis_rate_limit(tenant_id, bucket, max_requests, window_seconds)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

logger = structlog.get_logger(__name__)

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
    user_id: Any,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    ip: Optional[str] = None,
    pii_accessed: Optional[str] = None,
) -> None:
    """
    Persiste um registro de AuditLog. Faz flush mas não commit.

    Args:
        pii_accessed: Se não None, registra acesso a PII específico (ex: "cpf", "full_name").
                     Exemplo: pii_accessed="cpf" registra "ACCESS_PII: cpf acessado".
                     Exigido para LGPD Art. 37 (auditoria de acesso a dados pessoais).
    """
    from models import AuditLog  # importação local para evitar circular

    # Se PII foi acessado, registra explicitamente
    if pii_accessed:
        action = f"ACCESS_PII:{pii_accessed}"

    al = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
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
