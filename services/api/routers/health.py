"""
routers/health.py — Liveness and readiness probes.

GET /health/live   — trivial: always returns {"status": "live"}
GET /health/ready  — aggregate probe: checks Postgres, Redis, Kafka, MinIO,
                     ClickHouse, and ML service; returns 200 ok or 503 degraded.

The old GET /health in main.py is kept as a backward-compat alias to /health/live.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import settings
from database import AsyncSessionLocal

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["infra"])


@router.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "live"}


@router.get("/health/ready", tags=["infra"])
async def health_ready():
    """Aggregate readiness probe that checks all critical dependencies."""
    checks: dict[str, str] = {}

    # ── Postgres ──────────────────────────────────────────────────────────
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_postgres_failed", error=str(exc))
        checks["postgres"] = "error"

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await asyncio.wait_for(r.ping(), timeout=2.0)
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_redis_failed", error=str(exc))
        checks["redis"] = "error"

    # ── Kafka / Redpanda ──────────────────────────────────────────────────
    try:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers
        )
        await asyncio.wait_for(producer.start(), timeout=3.0)
        await producer.stop()
        checks["kafka"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_kafka_failed", error=str(exc))
        checks["kafka"] = "error"

    # ── MinIO ─────────────────────────────────────────────────────────────
    try:
        import httpx

        endpoint = settings.minio_endpoint.rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{endpoint}/minio/health/live")
        checks["minio"] = "ok" if resp.status_code < 500 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_minio_failed", error=str(exc))
        checks["minio"] = "error"

    # ── ClickHouse ────────────────────────────────────────────────────────
    try:
        import httpx

        ch_url = f"http://{settings.clickhouse_host}:{settings.clickhouse_port}/ping"
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(ch_url)
        checks["clickhouse"] = "ok" if resp.status_code == 200 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_clickhouse_failed", error=str(exc))
        checks["clickhouse"] = "error"

    # ── ML service ────────────────────────────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("http://ml_service:8001/health")
        checks["ml_service"] = "ok" if resp.status_code < 500 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_ml_service_failed", error=str(exc))
        checks["ml_service"] = "error"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        {
            "status": overall,
            "checks": checks,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        status_code=200 if overall == "ok" else 503,
    )
