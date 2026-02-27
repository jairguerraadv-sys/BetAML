"""JWT authentication + RBAC dependency."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

ROLES = {"ADMIN", "AML_ANALYST", "AUDITOR"}


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.access_token_expire_min))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.active:
        raise credentials_exception
    return user


def require_roles(*roles: str):
    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado para role '{current_user.role}'",
            )
        return current_user
    return checker


# PII Encryption (AES-256-CBC via Fernet-like using base64 XOR for dev;
# prod: use AWS KMS / Vault)
def encrypt_pii(plain: str) -> bytes:
    key = settings.pii_encryption_key.encode()[:32].ljust(32, b"0")
    data = plain.encode("utf-8")
    # Simple XOR with key cycling — substitua por Fernet/KMS em prod
    encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.b64encode(encrypted)


def decrypt_pii(ciphertext: bytes) -> str:
    key = settings.pii_encryption_key.encode()[:32].ljust(32, b"0")
    data = base64.b64decode(ciphertext)
    decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return decrypted.decode("utf-8")


def mask_cpf(cpf: str) -> str:
    """Exibe apenas os 3 últimos dígitos: ***.***.***-XX"""
    digits = "".join(c for c in cpf if c.isdigit())
    if len(digits) >= 3:
        return f"***.***.***.{digits[-2:]}"
    return "***"
