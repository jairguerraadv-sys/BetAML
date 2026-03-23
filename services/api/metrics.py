"""
metrics.py — Custom Prometheus business metrics for BetAML.

Gauges:
  betaml_open_alerts_total         — open alerts by severity and tenant
  betaml_ml_last_trained_seconds   — Unix timestamp of last model training per tenant
  betaml_ingest_errors_unresolved  — unresolved DLQ entries per tenant
  betaml_kafka_consumer_lag        — best-effort consumer lag per tenant (via Redpanda admin API)
"""
from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import Alert, IngestError, ModelRegistry

logger = structlog.get_logger(__name__)

OPEN_ALERTS_GAUGE = Gauge(
    "betaml_open_alerts_total",
    "Number of open alerts by severity and tenant",
    ["severity", "tenant_id"],
)

ML_LAST_TRAINED = Gauge(
    "betaml_ml_last_trained_seconds",
    "Unix timestamp of the most recent model training run",
    ["tenant_id"],
)

INGEST_ERR_GAUGE = Gauge(
    "betaml_ingest_errors_unresolved",
    "Number of unresolved ingest errors (DLQ) per tenant",
    ["tenant_id"],
)

KAFKA_LAG_GAUGE = Gauge(
    "betaml_kafka_consumer_lag",
    "Kafka consumer group lag per tenant (best-effort)",
    ["tenant_id"],
)

KAFKA_LAG_BY_GROUP_TOPIC = Gauge(
    "betaml_kafka_consumer_lag_messages",
    "Kafka consumer lag per consumer group and topic",
    ["group_id", "topic"],
)

EXTERNAL_VALIDATION_REQUESTS = Counter(
    "betaml_external_validation_requests_total",
    "Total de solicitações de validação externa por provider e tipo",
    ["provider", "validation_type"],
)

EXTERNAL_VALIDATION_RESULTS = Counter(
    "betaml_external_validation_results_total",
    "Total de resultados de validação externa por provider e status",
    ["provider", "status"],
)


def observe_external_validation_request(provider: str, validation_type: str) -> None:
    EXTERNAL_VALIDATION_REQUESTS.labels(provider=provider, validation_type=validation_type).inc()


def observe_external_validation_result(provider: str, status: str) -> None:
    EXTERNAL_VALIDATION_RESULTS.labels(provider=provider, status=status).inc()

async def update_business_metrics() -> None:
    """Update all business Prometheus gauges. Runs every 5 min via APScheduler."""
    try:
        async with AsyncSessionLocal() as db:
            # 1. Open alerts by severity per tenant
            alert_rows = (
                await db.execute(
                    select(Alert.tenant_id, Alert.severity, func.count(Alert.id).label("cnt"))
                    .where(Alert.status == "OPEN")
                    .group_by(Alert.tenant_id, Alert.severity)
                )
            ).all()
            for row in alert_rows:
                OPEN_ALERTS_GAUGE.labels(
                    severity=row.severity, tenant_id=row.tenant_id
                ).set(row.cnt)

            # 2. ML last trained per tenant
            ml_rows = (
                await db.execute(
                    select(
                        ModelRegistry.tenant_id,
                        func.max(ModelRegistry.trained_at).label("last_trained"),
                    ).group_by(ModelRegistry.tenant_id)
                )
            ).all()
            for row in ml_rows:
                if row.last_trained:
                    ML_LAST_TRAINED.labels(tenant_id=row.tenant_id).set(
                        row.last_trained.timestamp()
                    )

            # 3. Unresolved ingest errors per tenant
            err_rows = (
                await db.execute(
                    select(IngestError.tenant_id, func.count(IngestError.id).label("cnt"))
                    .where(IngestError.resolved.is_(False))
                    .group_by(IngestError.tenant_id)
                )
            ).all()
            for row in err_rows:
                INGEST_ERR_GAUGE.labels(tenant_id=row.tenant_id).set(row.cnt)

        logger.info("business_metrics_updated")
    except Exception as exc:  # noqa: BLE001
        logger.warning("business_metrics_update_failed", error=str(exc))

    # 4. Kafka lag — best-effort via Redpanda admin API
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://redpanda:9644/v1/consumer_groups")
            if resp.status_code == 200:
                data = resp.json()
                groups = data if isinstance(data, list) else data.get("consumer_groups", [])
                for group in groups:
                    lag = group.get("lag", 0)
                    # Derive tenant_id from consumer group name convention
                    name: str = group.get("group_id", "")
                    tenant_id = name.split(":")[0] if ":" in name else "default"
                    KAFKA_LAG_GAUGE.labels(tenant_id=tenant_id).set(lag)
                    topic = str(group.get("topic") or "all")
                    KAFKA_LAG_BY_GROUP_TOPIC.labels(group_id=name or "unknown", topic=topic).set(lag)
    except Exception:  # noqa: BLE001
        pass  # Kafka lag is best-effort — skip silently on connection error
