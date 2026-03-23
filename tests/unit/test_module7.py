"""
tests/unit/test_module7.py — Module 7 observability tests.

Covers:
  - health_live returns 200
  - health_ready returns structure with checks key
  - health_ready returns degraded when postgres fails
  - maintenance middleware exempts /health path
  - maintenance middleware exempts /auth path
  - maintenance middleware allows safe disable endpoint for admins
  - maintenance middleware returns 503 when enabled
  - maintenance middleware passes through when disabled
  - request_id middleware generates id if missing
  - request_id middleware preserves existing id
  - request_id header returned in response
  - open_alerts gauge metric is registered
  - update_business_metrics executes queries
  - ops summary exposes operational alerts
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
import json

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_asgi_scope(path: str = "/health/live", headers: list | None = None) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
    }


# ---------------------------------------------------------------------------
# 1–3: Health endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_live_returns_200():
    """GET /health/live always returns {"status": "live"} with 200."""
    from routers.health import health_live

    result = await health_live()
    assert result == {"status": "live"}


@pytest.mark.asyncio
async def test_health_ready_returns_structure_with_checks_key():
    """GET /health/ready returns a dict with 'checks' key."""
    from routers.health import health_ready

    # Patch all dependency checks to succeed
    _ok_client = AsyncMock()
    _ok_client.__aenter__ = AsyncMock(return_value=_ok_client)
    _ok_client.__aexit__ = AsyncMock(return_value=False)
    _ok_client.get = AsyncMock(return_value=MagicMock(status_code=200))
    _ok_client.execute = AsyncMock(return_value=MagicMock())
    _ok_client.ping = AsyncMock(return_value=True)
    _ok_client.aclose = AsyncMock()

    with patch("routers.health.AsyncSessionLocal", return_value=MagicMock(
        __aenter__=AsyncMock(return_value=_ok_client),
        __aexit__=AsyncMock(return_value=False),
    )), \
         patch("redis.asyncio.from_url", return_value=_ok_client), \
         patch("aiokafka.AIOKafkaProducer") as mock_producer_cls, \
         patch("httpx.AsyncClient") as mock_httpx:
        producer_inst = AsyncMock()
        producer_inst.start = AsyncMock()
        producer_inst.stop = AsyncMock()
        mock_producer_cls.return_value = producer_inst
        httpx_instance = AsyncMock()
        httpx_instance.__aenter__ = AsyncMock(return_value=httpx_instance)
        httpx_instance.__aexit__ = AsyncMock(return_value=False)
        httpx_instance.get = AsyncMock(return_value=MagicMock(status_code=200))
        mock_httpx.return_value = httpx_instance

        response = await health_ready()

    body = response.body
    import json
    data = json.loads(body)
    assert "checks" in data
    assert "status" in data
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_health_ready_degraded_when_postgres_fails():
    """health_ready must return status=degraded and 503 when Postgres is down."""
    from routers.health import health_ready

    with patch("routers.health.AsyncSessionLocal", side_effect=Exception("Connection refused")), \
         patch("redis.asyncio.from_url", return_value=AsyncMock(
             ping=AsyncMock(return_value=True),
             aclose=AsyncMock(),
         )), \
         patch("aiokafka.AIOKafkaProducer") as mock_producer_cls, \
         patch("httpx.AsyncClient") as mock_httpx:
        producer_inst = AsyncMock()
        producer_inst.start = AsyncMock()
        producer_inst.stop = AsyncMock()
        mock_producer_cls.return_value = producer_inst
        httpx_instance = AsyncMock()
        httpx_instance.__aenter__ = AsyncMock(return_value=httpx_instance)
        httpx_instance.__aexit__ = AsyncMock(return_value=False)
        httpx_instance.get = AsyncMock(return_value=MagicMock(status_code=200))
        mock_httpx.return_value = httpx_instance

        response = await health_ready()

    import json
    data = json.loads(response.body)
    assert data["status"] == "degraded"
    assert response.status_code == 503
    assert data["checks"]["postgres"] == "error"


# ---------------------------------------------------------------------------
# 4–7: MaintenanceModeMiddleware
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maintenance_middleware_exempts_health_path():
    """MaintenanceModeMiddleware must pass /health/* without any DB check."""
    from middleware import MaintenanceModeMiddleware
    request = Request(_make_asgi_scope("/health/live"))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw = MaintenanceModeMiddleware(app=MagicMock())
    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_middleware_exempts_auth_path():
    """MaintenanceModeMiddleware must pass /auth/* without any DB check."""
    from middleware import MaintenanceModeMiddleware
    request = Request(_make_asgi_scope("/auth/login"))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw = MaintenanceModeMiddleware(app=MagicMock())
    resp = await mw.dispatch(request, call_next)
    assert resp.status_code == 200
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_middleware_allows_admin_disable_request_when_enabled():
    """Admins must be able to turn maintenance off even while it is active."""
    import middleware as mw
    from middleware import MaintenanceModeMiddleware

    from jose import jwt as _jwt
    token = _jwt.encode(
        {"tenant_id": "t1", "sub": "u1", "role": "ADMIN"},
        "dev-secret-change-me",
        algorithm="HS256",
    )
    request = Request(_make_asgi_scope(
        "/admin/maintenance-mode",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    ) | {"query_string": b"enabled=false", "method": "PUT"})
    call_next = AsyncMock(return_value=PlainTextResponse("disabled"))
    mw_instance = MaintenanceModeMiddleware(app=MagicMock())

    with patch.object(mw, "_is_maintenance_enabled", AsyncMock(return_value=True)):
        resp = await mw_instance.dispatch(request, call_next)

    assert resp.status_code == 200
    assert resp.body.decode() == "disabled"
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_maintenance_middleware_returns_503_when_enabled():
    """MaintenanceModeMiddleware returns 503 for authenticated requests when flag is set."""
    import middleware as mw
    from middleware import MaintenanceModeMiddleware

    # Build a valid-ish JWT so middleware extracts tenant_id
    from jose import jwt as _jwt
    token = _jwt.encode({"tenant_id": "t1", "sub": "u1"}, "dev-secret-change-me", algorithm="HS256")
    request = Request(_make_asgi_scope(
        "/rules",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    ))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw_instance = MaintenanceModeMiddleware(app=MagicMock())

    with patch.object(mw, "_is_maintenance_enabled", AsyncMock(return_value=True)):
        resp = await mw_instance.dispatch(request, call_next)

    assert resp.status_code == 503
    assert "manutenção" in json.loads(resp.body)["detail"].lower()
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_middleware_still_blocks_non_disable_admin_request_when_enabled():
    """Only the explicit disable flow should bypass maintenance mode."""
    import middleware as mw
    from middleware import MaintenanceModeMiddleware

    from jose import jwt as _jwt
    token = _jwt.encode(
        {"tenant_id": "t1", "sub": "u1", "role": "ADMIN"},
        "dev-secret-change-me",
        algorithm="HS256",
    )
    request = Request(_make_asgi_scope(
        "/admin/maintenance-mode",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    ) | {"query_string": b"enabled=true", "method": "PUT"})
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw_instance = MaintenanceModeMiddleware(app=MagicMock())

    with patch.object(mw, "_is_maintenance_enabled", AsyncMock(return_value=True)):
        resp = await mw_instance.dispatch(request, call_next)

    assert resp.status_code == 503
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_middleware_passthrough_when_disabled():
    """MaintenanceModeMiddleware passes request when maintenance is off."""
    import middleware as mw
    from middleware import MaintenanceModeMiddleware

    from jose import jwt as _jwt
    token = _jwt.encode({"tenant_id": "t1", "sub": "u1"}, "dev-secret-change-me", algorithm="HS256")
    request = Request(_make_asgi_scope(
        "/rules",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    ))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw_instance = MaintenanceModeMiddleware(app=MagicMock())

    with patch.object(mw, "_is_maintenance_enabled", AsyncMock(return_value=False)):
        resp = await mw_instance.dispatch(request, call_next)

    assert resp.status_code == 200
    call_next.assert_awaited_once()


# ---------------------------------------------------------------------------
# 8–10: RequestIDMiddleware
# ---------------------------------------------------------------------------

def _build_request_id_app():
    from middleware import RequestIDMiddleware
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import PlainTextResponse

    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ping", endpoint)])
    app.add_middleware(RequestIDMiddleware)
    return app


@pytest.mark.asyncio
async def test_request_id_middleware_generates_id_if_missing():
    """RequestIDMiddleware generates an X-Request-ID if not present in the request."""
    from middleware import RequestIDMiddleware

    request = Request(_make_asgi_scope("/ping"))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw = RequestIDMiddleware(app=MagicMock())
    resp = await mw.dispatch(request, call_next)
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) == 36  # UUID4 length


@pytest.mark.asyncio
async def test_request_id_middleware_preserves_existing_id():
    """RequestIDMiddleware preserves caller-supplied X-Request-ID header."""
    from middleware import RequestIDMiddleware

    request = Request(_make_asgi_scope("/ping", headers=[(b"x-request-id", b"my-trace-id-123")]))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw = RequestIDMiddleware(app=MagicMock())
    resp = await mw.dispatch(request, call_next)
    assert resp.headers["x-request-id"] == "my-trace-id-123"


@pytest.mark.asyncio
async def test_request_id_header_returned_in_response():
    """RequestIDMiddleware always echoes X-Request-ID back in the response."""
    from middleware import RequestIDMiddleware

    request = Request(_make_asgi_scope("/ping"))
    call_next = AsyncMock(return_value=PlainTextResponse("ok"))
    mw = RequestIDMiddleware(app=MagicMock())
    resp = await mw.dispatch(request, call_next)
    assert "x-request-id" in resp.headers


# ---------------------------------------------------------------------------
# 11–12: Prometheus metrics
# ---------------------------------------------------------------------------

def test_open_alerts_gauge_metric_registered():
    """OPEN_ALERTS_GAUGE Prometheus metric must be registered."""
    from prometheus_client import REGISTRY
    from metrics import OPEN_ALERTS_GAUGE, INGEST_ERR_GAUGE, ML_LAST_TRAINED, KAFKA_LAG_BY_GROUP_TOPIC

    names = [m.name for m in REGISTRY.collect()]
    assert "betaml_open_alerts_total" in names
    assert "betaml_ingest_errors_unresolved" in names
    assert "betaml_ml_last_trained_seconds" in names
    assert "betaml_kafka_consumer_lag_messages" in names


@pytest.mark.asyncio
async def test_business_metrics_update_executes_queries():
    """update_business_metrics must execute at least 3 DB queries (alerts, ml, errors)."""
    from metrics import update_business_metrics

    executed = []

    async def _execute(stmt, *a, **kw):
        executed.append(str(stmt))
        r = MagicMock()
        r.all.return_value = []
        return r

    db_mock = AsyncMock()
    db_mock.execute = AsyncMock(side_effect=_execute)
    db_mock.__aenter__ = AsyncMock(return_value=db_mock)
    db_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("metrics.AsyncSessionLocal", return_value=db_mock), \
         patch("httpx.AsyncClient") as mock_httpx:
        httpx_instance = AsyncMock()
        httpx_instance.__aenter__ = AsyncMock(return_value=httpx_instance)
        httpx_instance.__aexit__ = AsyncMock(return_value=False)
        httpx_instance.get = AsyncMock(side_effect=Exception("offline"))
        mock_httpx.return_value = httpx_instance

        await update_business_metrics()

    assert len(executed) >= 3


@pytest.mark.asyncio
async def test_admin_ops_summary_returns_operational_alerts():
    """Admin ops summary should surface lag / DLQ / stale-model alerts when thresholds are crossed."""
    from routers.admin import get_ops_summary

    db = AsyncMock()
    current_user = MagicMock()
    current_user.tenant_id = "t1"
    current_user.id = "u1"

    maintenance_flag = MagicMock()
    maintenance_flag.value = {"enabled": False}
    db.get = AsyncMock(return_value=maintenance_flag)

    responses = [
        5,          # unresolved_dlq
        (95, 10),   # processed_24h, failed_24h
        2,          # stale_models
        None,       # oldest_model_dt
    ]

    async def _execute(stmt, *a, **kw):
        value = responses.pop(0)
        result = MagicMock()
        result.scalar.return_value = value
        result.one.return_value = value
        return result

    db.execute = AsyncMock(side_effect=_execute)

    with patch("httpx.AsyncClient") as mock_httpx:
        httpx_instance = AsyncMock()
        httpx_instance.__aenter__ = AsyncMock(return_value=httpx_instance)
        httpx_instance.__aexit__ = AsyncMock(return_value=False)
        httpx_instance.get = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: [{"group_id": "rules-engine", "lag": 2500}]))
        mock_httpx.return_value = httpx_instance

        result = await get_ops_summary(db=db, current_user=current_user)

    assert result.kafka_consumer_lag == 2500
    assert result.unresolved_dlq_events == 5
    assert result.stale_models == 2
    assert len(result.alerts) >= 3
