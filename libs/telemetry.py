"""
telemetry.py — OpenTelemetry bootstrap for distributed tracing.

Behavior:
  - When OTEL_EXPORTER_OTLP_ENDPOINT is set: initialises a real TracerProvider
    with OTLP/gRPC exporter, resource attributes, and batch span processor.
  - When the env var is absent: falls back to a no-op stub so services start
    without tracing overhead.

The public API is **unchanged** — callers still do:

    from libs.telemetry import init_opentelemetry_stub
    init_opentelemetry_stub("api")

Environment variables:
  OTEL_EXPORTER_OTLP_ENDPOINT  — e.g. http://jaeger:4317
  OTEL_TRACES_SAMPLER           — parentbased_traceidratio (default)
  OTEL_TRACES_SAMPLER_ARG       — 1.0 (default; use 0.1 in prod for 10%)
  ENVIRONMENT                   — added as resource attribute
"""
from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)

# Re-export a convenience handle; callers can do `from libs.telemetry import get_tracer`
_tracer = None


def get_tracer():
    """Return the global tracer (or a no-op fallback)."""
    global _tracer  # noqa: PLW0603
    if _tracer is not None:
        return _tracer
    try:
        from opentelemetry import trace
        _tracer = trace.get_tracer("betaml")
    except ImportError:
        _tracer = _NoOpTracer()
    return _tracer


class _NoOpSpan:
    """Minimal no-op span for environments without OTel SDK."""
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def set_attribute(self, *_):
        pass
    def set_status(self, *_):
        pass
    def add_event(self, *_):
        pass


class _NoOpTracer:
    """Minimal no-op tracer fallback."""
    def start_as_current_span(self, name, **_):
        return _NoOpSpan()


def init_opentelemetry_stub(service_name: str) -> dict[str, str | bool | None]:
    """Initialise OpenTelemetry tracing if OTEL_EXPORTER_OTLP_ENDPOINT is set.

    Maintains backward-compatible signature and return value.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    enabled = bool(endpoint)

    if enabled:
        _init_real_otel(service_name, endpoint)
    else:
        logger.info(
            "opentelemetry_noop",
            service_name=service_name,
            hint="Set OTEL_EXPORTER_OTLP_ENDPOINT to enable tracing",
        )

    payload = {
        "service_name": service_name,
        "otel_enabled": enabled,
        "otlp_endpoint": endpoint,
    }
    logger.info("opentelemetry_initialized", **payload)
    return payload


def _init_real_otel(service_name: str, endpoint: str) -> None:
    """Wire up TracerProvider + OTLP exporter + BatchSpanProcessor."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        logger.warning(
            "opentelemetry_sdk_missing",
            hint="Install: pip install opentelemetry-api opentelemetry-sdk "
                 "opentelemetry-exporter-otlp-proto-grpc",
        )
        return

    environment = os.getenv("ENVIRONMENT", "development")
    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "betaml",
        "deployment.environment": environment,
    })

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    global _tracer  # noqa: PLW0603
    _tracer = trace.get_tracer("betaml")

    logger.info(
        "opentelemetry_real_provider_configured",
        service_name=service_name,
        endpoint=endpoint,
        environment=environment,
    )
