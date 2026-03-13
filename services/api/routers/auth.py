"""routers/auth.py — Autenticação: login, refresh, logout, /me"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    create_access_token,
    get_current_user,
    oauth2_scheme,
    require_roles,
    revoke_token,
    verify_password,
)
from config import settings
from database import get_db
from models import Tenant, User
from rate_limit import limiter

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str


class LoginRequest(BaseModel):
    username: str
    password: str
    tenant_slug: Optional[str] = None


@router.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)):
    if body.tenant_slug:
        tenant_result = await db.execute(
            select(Tenant).where(Tenant.slug == body.tenant_slug, Tenant.active == True)
        )
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
        result = await db.execute(
            select(User).where(
                User.username == body.username,
                User.tenant_id == tenant.id,
                User.active == True,
            )
        )
    else:
        result = await db.execute(
            select(User).where(User.username == body.username, User.active == True)
        )
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
    token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )
    return TokenResponse(access_token=token, role=user.role, tenant_id=user.tenant_id)


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id,
    }


@router.post("/auth/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh(request: Request, current_user: User = Depends(get_current_user)):
    token = create_access_token({
        "sub": current_user.id,
        "tenant_id": current_user.tenant_id,
        "role": current_user.role,
    })
    return TokenResponse(access_token=token, role=current_user.role, tenant_id=current_user.tenant_id)


@router.post("/auth/logout")
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
):
    from jose import JWTError
    from jose import jwt as _jwt
    try:
        payload = _jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            await revoke_token(jti, exp)
    except JWTError:
        pass
    return {"message": "Logout realizado"}
