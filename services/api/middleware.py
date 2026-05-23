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


def _decode_auth_payload(token: str) -> dict | None:
    try:
        return _jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        if settings.environment not in {"development", "test"}:
            return None
        try:
            return _jwt.decode(
                token,
                "dev-secret-change-me",
                algorithms=[settings.jwt_algorithm],
            )
        except JWTError:
            return None


def _is_safe_maintenance_disable_request(request: Request, payload: dict | None) -> bool:
    """Allow admins to disable maintenance mode even while it is active.

    Without this exception the tenant can be locked in maintenance until the
    TTL cache expires, because the middleware blocks the same endpoint used to
    turn maintenance off.
    """
    if request.url.path != "/admin/maintenance-mode":
        return False
    if request.method.upper() not in {"POST", "PUT"}:
        return False
    if str(request.query_params.get("enabled", "")).lower() != "false":
        return False
    if not payload:
        return False
    token_roles = set(payload.get("roles") or [])
    legacy_role = str(payload.get("role") or "")
    token_roles.add(legacy_role)
    return bool(token_roles.intersection({"ADMIN", "SUPER_ADMIN", "Operador_AdminTecnico", "BetAML_SuperAdmin"}))


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
                        SystemFlag.tenant_id == tenant_id,
                        SystemFlag.flag_name == "maintenance_mode",
                    )
                )
            ).scalar_one_or_none()
            if flag and isinstance(flag.flag_value, dict):
                enabled = bool(flag.flag_value.get("enabled", False))
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

        payload = _decode_auth_payload(auth_header[7:])
        tenant_id: str | None = payload.get("tenant_id") if payload else None

        if tenant_id and await _is_maintenance_enabled(tenant_id):
            if _is_safe_maintenance_disable_request(request, payload):
                return await call_next(request)
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
