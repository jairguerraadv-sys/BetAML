"""routers/auth.py — Autenticação: login, refresh, logout, /me"""
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    oauth2_scheme_optional,
    revoke_refresh_token,
    revoke_token,
    store_refresh_token_jti,
    verify_password,
)
from config import settings
from database import get_db
from models import Tenant, User
from rate_limit import get_rate_limit_by_role, limiter
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])
# Rate-limit: 10/min por IP no login e refresh.
# O decorator @limiter.limit requer `request: Request` como primeiro parâmetro
# explícito para funcionar corretamente com `from __future__ import annotations`.


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None  # Incluído para mobile apps
    token_type: str = "bearer"
    role: str
    tenant_id: str


class LoginRequest(BaseModel):
    username: str
    password: str
    tenant_slug: Optional[str] = None


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Login endpoint with refresh token rotation.

    - Creates access token (15min) and refresh token (7d sliding window)
    - Sets httpOnly cookies for web clients
    - Returns tokens in body for mobile apps
    - Audit logs LOGIN/LOGIN_FAILED events
    """
    if body.tenant_slug:
        tenant_result = await db.execute(
            select(Tenant).where(Tenant.slug == body.tenant_slug, Tenant.active.is_(True))
        )
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
        result = await db.execute(
            select(User).where(
                User.username == body.username,
                User.tenant_id == tenant.id,
                User.active.is_(True),
            )
        )
    else:
        result = await db.execute(
            select(User).where(User.username == body.username, User.active.is_(True))
        )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")

    ip = str(request.client.host) if request.client else None

    if not verify_password(body.password, user.password_hash):
        await write_audit(db, user.tenant_id, user.id, "LOGIN_FAILED", "User", str(user.id), ip=ip)
        await db.commit()
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")

    # Create access token (15min)
    access_token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )

    # Create refresh token (7d sliding window)
    refresh_token, refresh_jti = create_refresh_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )

    # Store refresh JTI in users table (invalidates previous)
    await store_refresh_token_jti(db, user.id, refresh_jti)

    # Set httpOnly cookies (secure in production)
    response.set_cookie(
        key="betaml_token",
        value=access_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=900  # 15min in seconds
    )

    response.set_cookie(
        key="betaml_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=7 * 24 * 3600  # 7 days
    )

    # Audit log
    await write_audit(db, user.tenant_id, user.id, "LOGIN", "User", str(user.id), ip=ip)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,  # Also return in body for mobile apps
        role=user.role,
        tenant_id=user.tenant_id
    )


@router.get("/me")
@router.get("/auth/me")
@limiter.limit("100/minute")
async def me(request: Request, current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id,
    }


@router.post("/auth/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    refresh_token = request.cookies.get("betaml_refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token ausente")

    try:
        payload = jwt.decode(refresh_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Refresh token inválido") from exc

    if payload.get("token_type") != "refresh":
        raise HTTPException(status_code=401, detail="Tipo de token inválido para refresh")

    user_id = payload.get("sub")
    jti = payload.get("jti")
    if not user_id or not jti:
        raise HTTPException(status_code=401, detail="Refresh token inválido")

    user = (
        (
            await db.execute(
                select(User).where(User.id == user_id, User.active.is_(True))
            )
        )
        .scalar_one_or_none()
    )
    if not user or user.refresh_token_jti != jti:
        raise HTTPException(status_code=401, detail="Refresh token revogado ou desatualizado")

    new_access_token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )
    new_refresh_token, new_refresh_jti = create_refresh_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )
    await store_refresh_token_jti(db, user.id, new_refresh_jti)

    response.set_cookie(
        key="betaml_token",
        value=new_access_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=900,
    )
    response.set_cookie(
        key="betaml_refresh_token",
        value=new_refresh_token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=7 * 24 * 3600,
    )

    await write_audit(db, user.tenant_id, user.id, "TOKEN_REFRESH", "User", str(user.id))

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        role=user.role,
        tenant_id=user.tenant_id,
    )


@router.post("/auth/logout")
async def logout(
    request: Request,
    response: Response,
    token: str | None = Depends(oauth2_scheme_optional),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if token:
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                await revoke_token(jti, exp)
        except JWTError:
            pass

    await revoke_refresh_token(db, current_user.id)
    response.delete_cookie(key="betaml_token")
    response.delete_cookie(key="betaml_refresh_token")
    await write_audit(db, current_user.tenant_id, current_user.id, "LOGOUT", "User", str(current_user.id))
    return {"message": "Logout realizado"}
