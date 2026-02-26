import uuid
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.tenant import User, UserRole
from app.services.auth_service import decode_token

bearer_scheme = HTTPBearer()


class CurrentUser:
    """Dependency holder for the authenticated user context."""

    def __init__(self, user: User, tenant_id: uuid.UUID) -> None:
        self.user = user
        self.tenant_id = tenant_id


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not an access token")

    user_id_str = payload.get("sub")
    tenant_id_str = payload.get("tenant_id")
    if not user_id_str or not tenant_id_str:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token claims")

    try:
        user_id = uuid.UUID(user_id_str)
        tenant_id = uuid.UUID(tenant_id_str)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")

    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Ensure tenant_id in token matches the user's actual tenant
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant mismatch")

    return CurrentUser(user=user, tenant_id=tenant_id)


def require_roles(*roles: UserRole):
    """Dependency factory that checks the user has one of the specified roles."""

    async def _check(current: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if current.user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {current.user.role} is not permitted for this action",
            )
        return current

    return _check


# Convenience role dependencies
require_admin = require_roles(UserRole.ADMIN)
require_analyst_or_admin = require_roles(UserRole.AML_ANALYST, UserRole.ADMIN)
require_any_role = require_roles(UserRole.ADMIN, UserRole.AML_ANALYST, UserRole.AUDITOR)
require_auditor_or_admin = require_roles(UserRole.AUDITOR, UserRole.ADMIN)
