import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.tenant import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(subject: str, tenant_id: str, role: str) -> str:
    expire = _now_utc() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": _now_utc(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, tenant_id: str, role: str) -> str:
    expire = _now_utc() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "exp": expire,
        "iat": _now_utc(),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email, User.is_active == True))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user


_REFRESH_TOKEN_PREFIX = "refresh_blacklist:"


async def blacklist_refresh_token(redis: Redis, token: str) -> None:
    try:
        payload = decode_token(token)
        jti = payload.get("jti")
        exp = payload.get("exp")
        if jti and exp:
            ttl = int(exp - _now_utc().timestamp())
            if ttl > 0:
                await redis.setex(f"{_REFRESH_TOKEN_PREFIX}{jti}", ttl, "1")
    except ValueError:
        pass


async def is_refresh_token_blacklisted(redis: Redis, token: str) -> bool:
    try:
        payload = decode_token(token)
        jti = payload.get("jti")
        if not jti:
            return False
        return await redis.exists(f"{_REFRESH_TOKEN_PREFIX}{jti}") > 0
    except ValueError:
        return True
