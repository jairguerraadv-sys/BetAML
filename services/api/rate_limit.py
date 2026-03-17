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


ROLE_LIMITS_PER_MINUTE: dict[str, str] = {
    "SUPER_ADMIN": "100/minute",
    "ADMIN": "100/minute",
    "AML_ANALYST": "50/minute",
    "AUDITOR": "20/minute",
}


def get_rate_limit_by_role(request: Request) -> str:
    """Return role-based rate limit, with anonymous fallback.

    Priority:
    1) request.state.user_role (set by middleware)
    2) role claim from JWT
    3) anonymous default
    """
    role = getattr(request.state, "user_role", None)
    if role in ROLE_LIMITS_PER_MINUTE:
        return ROLE_LIMITS_PER_MINUTE[role]

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            from jose import jwt as _jwt

            payload = _jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            jwt_role = payload.get("role")
            if jwt_role in ROLE_LIMITS_PER_MINUTE:
                return ROLE_LIMITS_PER_MINUTE[jwt_role]
        except Exception:
            pass

    return "10/minute"


def _get_rate_limit_key(request: Request) -> str:
    """
    Rate limit key strategy: prefer tenant_id from JWT, fallback to IP.
    This ensures authenticated tenants share a per-tenant quota.
    """
    # Try to extract tenant_id and role from JWT
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            from jose import jwt as _jwt
            payload = _jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            tenant_id = payload.get("tenant_id")
            role = payload.get("role")
            ip = get_remote_address(request)
            if tenant_id:
                return f"role:{role or 'AUTH'}:tenant:{tenant_id}:ip:{ip}"
        except Exception:
            pass
    # Fallback to IP-based rate limiting
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_get_rate_limit_key,
    storage_uri=settings.redis_url,
    default_limits=["1000/minute", "10000/hour"],
)
