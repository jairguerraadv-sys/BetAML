from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, get_current_user
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    authenticate_user,
    blacklist_refresh_token,
    create_access_token,
    create_refresh_token,
    decode_token,
    is_refresh_token_blacklisted,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_redis(request: Request) -> Redis:
    return request.app.state.redis


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> TokenResponse:
    user = await authenticate_user(db, body.email, body.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(str(user.id), str(user.tenant_id), user.role.value)
    refresh_token = create_refresh_token(str(user.id), str(user.tenant_id), user.role.value)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AccessTokenResponse:
    redis: Redis = _get_redis(request)

    if await is_refresh_token_blacklisted(redis, body.refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked")

    try:
        payload = decode_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token")

    access_token = create_access_token(payload["sub"], payload["tenant_id"], payload["role"])
    return AccessTokenResponse(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, request: Request) -> None:
    redis: Redis = _get_redis(request)
    await blacklist_refresh_token(redis, body.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(current: Annotated[CurrentUser, Depends(get_current_user)]) -> UserResponse:
    return UserResponse.model_validate(current.user)
