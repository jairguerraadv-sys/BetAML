"""routers/auth.py — Autenticação: login, refresh, logout, /me"""
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
    revoke_token,
    verify_password,
)
from config import settings
from database import get_db
from models import Tenant, User
from rate_limit import limiter
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["auth"])
# Rate-limit: 10/min por IP no login e refresh.
# O decorator @limiter.limit requer `request: Request` como primeiro parâmetro
# explícito para funcionar corretamente com `from __future__ import annotations`.


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
    await write_audit(db, user.tenant_id, user.id, "LOGIN", "User", str(user.id), ip=ip)
    token = create_access_token(
        {"sub": user.id, "tenant_id": user.tenant_id, "role": user.role}
    )
    return TokenResponse(access_token=token, role=user.role, tenant_id=user.tenant_id)


@router.get("/me")
@router.get("/auth/me")
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id,
    }


@router.post("/auth/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
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
    db: AsyncSession = Depends(get_db),
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
    await write_audit(db, current_user.tenant_id, current_user.id, "LOGOUT", "User", str(current_user.id))
    return {"message": "Logout realizado"}
