"""
middleware.py — Custom ASGI middlewares for BetAML.

RequestIDMiddleware:
  - Reads X-Request-ID from incoming request; generates uuid4() if absent.
  - Clears structlog contextvars and binds request_id for correlated logging.
  - Injects the same ID into the response X-Request-ID header.

MaintenanceModeMiddleware:
  - Skips exempt paths (/health, /auth, /metrics, /docs, /redoc, /openapi.json).
  - Decodes JWT to obtain tenant_id.
  - Checks SystemFlag {tenant_id}:maintenance_mode using an in-process 60s TTL cache.
  - Returns 503 JSON response if maintenance is enabled.
"""
from __future__ import annotations

import time
import uuid

import structlog
from jose import JWTError
from jose import jwt as _jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from config import settings
from database import AsyncSessionLocal

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# RequestIDMiddleware
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach/generate X-Request-ID and bind it to structlog contextvars."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# MaintenanceModeMiddleware
# ---------------------------------------------------------------------------

_EXEMPT_PREFIXES = (
    "/health",
    "/auth",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# In-process cache: tenant_id -> (is_enabled, expires_at_unix)
_maintenance_cache: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 60.0  # seconds


async def _is_maintenance_enabled(tenant_id: str) -> bool:
    """Return True if maintenance mode is active for the tenant.

    Uses an in-process dict cache with 60-second TTL to avoid a DB roundtrip
    on every request. Safe for single-process deployments; for multi-replica
    deployments, use Redis as the backing store instead.
    """
    now = time.monotonic()
    cached = _maintenance_cache.get(tenant_id)
    if cached and now < cached[1]:
        return cached[0]

    # Cache miss or expired — query the DB
    enabled = False
    try:
        from sqlalchemy import select
        from models import SystemFlag

        async with AsyncSessionLocal() as db:
            flag = (
                await db.execute(
                    select(SystemFlag).where(
                        SystemFlag.key == f"{tenant_id}:maintenance_mode"
                    )
                )
            ).scalar_one_or_none()
            if flag and isinstance(flag.value, dict):
                enabled = bool(flag.value.get("enabled", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("maintenance_flag_lookup_failed", tenant_id=tenant_id, error=str(exc))

    _maintenance_cache[tenant_id] = (enabled, now + _CACHE_TTL)
    return enabled


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """Return 503 for authenticated requests when maintenance mode is active."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return await call_next(request)

        tenant_id: str | None = None
        try:
            payload = _jwt.decode(
                auth_header[7:],
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            tenant_id = payload.get("tenant_id")
        except JWTError:
            if settings.environment in ("development", "test"):
                try:
                    payload = _jwt.decode(
                        auth_header[7:],
                        "dev-secret-change-me",
                        algorithms=[settings.jwt_algorithm],
                    )
                    tenant_id = payload.get("tenant_id")
                except JWTError:
                    pass

        if tenant_id and await _is_maintenance_enabled(tenant_id):
            return JSONResponse(
                {
                    "detail": (
                        "Sistema em manutenção. Tente novamente em alguns instantes. "
                        "Se o problema persistir, contate o suporte."
                    )
                },
                status_code=503,
            )

        return await call_next(request)
