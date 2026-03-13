"""
rate_limit.py — Shared rate limiter configuration for BetAML API.

Usage in routers:
    from rate_limit import limiter

    @router.post("/endpoint")
    @limiter.limit("100/minute")
    async def my_endpoint(request: Request, ...):
        ...
"""
from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings


def _get_rate_limit_key(request: Request) -> str:
    """
    Rate limit key strategy: prefer tenant_id from JWT, fallback to IP.
    This ensures authenticated tenants share a per-tenant quota.
    """
    # Try to extract tenant_id from JWT
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            from jose import jwt as _jwt
            payload = _jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
            tenant_id = payload.get("tenant_id")
            if tenant_id:
                return f"tenant:{tenant_id}"
        except Exception:
            pass
    # Fallback to IP-based rate limiting
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_get_rate_limit_key,
    storage_uri=settings.redis_url,
    default_limits=["1000/minute", "10000/hour"],
)
