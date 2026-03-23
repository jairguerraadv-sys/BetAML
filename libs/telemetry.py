"""
telemetry.py — lightweight tracing bootstrap stubs.

Current behavior:
  - logs whether an OTLP endpoint was configured
  - keeps startup contract stable for future Jaeger/Zipkin/OpenTelemetry wiring
"""
from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)


def init_opentelemetry_stub(service_name: str) -> dict[str, str | bool | None]:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    enabled = bool(endpoint)
    payload = {
        "service_name": service_name,
        "otel_enabled": enabled,
        "otlp_endpoint": endpoint,
    }
    logger.info("opentelemetry_stub_initialized", **payload)
    return payload
