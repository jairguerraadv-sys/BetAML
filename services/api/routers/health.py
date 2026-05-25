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

VALID_ALERT_STATUSES = (
    "OPEN",
    "IN_REVIEW",
    "CONFIRMED",
    "DISMISSED",
    "CLOSED",
    "FALSE_POSITIVE",
)


async def _run_health_checks() -> dict[str, str]:
    checks: dict[str, str] = {}
    data_quality_failures: list[str] = []

    # ── Postgres ──────────────────────────────────────────────────────────
    try:
        from sqlalchemy import text

        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_postgres_failed", error=str(exc))
        checks["postgres"] = "error"

    # ── Data Quality (OPS1) ────────────────────────────────────────────────
    # Reusa os checks críticos do script scripts/data_quality_checks.py para
    # integrar qualidade de dados ao readiness endpoint.
    try:
        from sqlalchemy import bindparam, text

        async with AsyncSessionLocal() as db:
            players_without_tenant = (
                await asyncio.wait_for(
                    db.execute(text("SELECT COUNT(*) FROM players WHERE tenant_id IS NULL")),
                    timeout=2.0,
                )
            ).scalar_one()
            alerts_invalid_status = (
                await asyncio.wait_for(
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM alerts "
                            "WHERE status NOT IN :valid_statuses"
                        ).bindparams(bindparam("valid_statuses", expanding=True)),
                        {"valid_statuses": VALID_ALERT_STATUSES},
                    ),
                    timeout=2.0,
                )
            ).scalar_one()
            snapshots_missing_version = (
                await asyncio.wait_for(
                    db.execute(text("SELECT COUNT(*) FROM feature_snapshots WHERE feature_version IS NULL")),
                    timeout=2.0,
                )
            ).scalar_one()
            unresolved_ingest_errors_24h = (
                await asyncio.wait_for(
                    db.execute(
                        text(
                            "SELECT COUNT(*) FROM ingest_errors "
                            "WHERE resolved = false AND created_at < (now() - interval '24 hours')"
                        )
                    ),
                    timeout=2.0,
                )
            ).scalar_one()

        failures = []
        if int(players_without_tenant or 0) > 0:
            failures.append("players_without_tenant")
        if int(alerts_invalid_status or 0) > 0:
            failures.append("alerts_invalid_status")
        if int(snapshots_missing_version or 0) > 0:
            failures.append("feature_snapshots_missing_version")
        # Readiness é requisito de corte: qualquer backlog antigo não resolvido
        # em ingestão bloqueia o status "ready".
        if int(unresolved_ingest_errors_24h or 0) > 0:
            failures.append("unresolved_ingest_errors_24h")

        data_quality_failures = failures
        checks["data_quality"] = "ok" if not failures else "error"
        if failures:
            logger.warning("health_check_data_quality_failed", failures=failures)
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_data_quality_probe_failed", error=str(exc))
        checks["data_quality"] = "error"
        data_quality_failures = ["data_quality_probe_failed"]

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
        from clickhouse_driver import Client

        def _probe_clickhouse() -> bool:
            client = Client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                database=settings.clickhouse_db,
                user=getattr(settings, "clickhouse_user", "default"),
                password=getattr(settings, "clickhouse_password", ""),
            )
            rows = client.execute("SELECT 1")
            return bool(rows and rows[0][0] == 1)

        is_ok = await asyncio.wait_for(asyncio.to_thread(_probe_clickhouse), timeout=2.0)
        checks["clickhouse"] = "ok" if is_ok else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_clickhouse_failed", error=str(exc))
        checks["clickhouse"] = "error"

    # ── ML service ────────────────────────────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.ml_service_url.rstrip('/')}/health")
        checks["ml_service"] = "ok" if resp.status_code < 500 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_ml_service_failed", error=str(exc))
        checks["ml_service"] = "error"

    # ── Rules Engine metrics endpoint ─────────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(settings.rules_engine_metrics_url)
        checks["rules_engine"] = "ok" if resp.status_code < 500 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_rules_engine_failed", error=str(exc))
        checks["rules_engine"] = "error"

    # ── Stream Processor metrics endpoint ─────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(settings.stream_processor_metrics_url)
        checks["stream_processor"] = "ok" if resp.status_code < 500 else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("health_check_stream_processor_failed", error=str(exc))
        checks["stream_processor"] = "error"

    if data_quality_failures:
        checks["data_quality_cutoff"] = "blocked"
    else:
        checks["data_quality_cutoff"] = "ok"
    return checks


@router.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "live"}


@router.get("/health/ready", tags=["infra"])
async def health_ready():
    """Aggregate readiness probe that checks all critical dependencies."""
    checks = await _run_health_checks()
    readiness_values = {
        k: v for k, v in checks.items()
        if k not in {"data_quality", "data_quality_cutoff"}
    }
    overall = "ok" if all(v == "ok" for v in readiness_values.values()) else "degraded"
    cutoff_blocked = checks.get("data_quality_cutoff") == "blocked"
    cutoff_reasons: list[str] = []
    if checks.get("data_quality") != "ok":
        cutoff_reasons.append("data_quality")
    return JSONResponse(
        {
            "status": overall,
            "checks": checks,
            "release_cutoff_blocked": cutoff_blocked,
            "release_cutoff_reasons": cutoff_reasons,
            "timestamp": datetime.now(UTC).isoformat(),
        },
        status_code=200 if overall == "ok" else 503,
    )
