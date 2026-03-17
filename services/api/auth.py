"""JWT authentication + RBAC dependency."""
from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import ApiKey, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

ROLES = {"SUPER_ADMIN", "ADMIN", "AML_ANALYST", "AUDITOR"}

# ── Redis client para blacklist de JWT ────────────────────────────────────────
_auth_redis: Any = None


async def _get_auth_redis():
    """Retorna conexão Redis (singleton lazy) para verificação de blacklist."""
    global _auth_redis
    if _auth_redis is None:
        try:
            import redis.asyncio as aioredis  # type: ignore
            _auth_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await _auth_redis.ping()
        except Exception:
            _auth_redis = None
    return _auth_redis


async def revoke_token(jti: str, exp: int) -> None:
    """Adiciona o jti à blacklist do Redis com TTL = tempo restante do token."""
    try:
        r = await _get_auth_redis()
        if r:
            ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
            await r.set(f"betaml:revoked:jti:{jti}", "1", ex=ttl)
    except Exception:
        pass  # não bloquear logout por falha de Redis


async def revoke_refresh_token(db: AsyncSession, user_id: str) -> None:
    """Revoke refresh token by nullifying refresh_token_jti in users table.

    Args:
        db: Database session
        user_id: UUID of the user
    """
    from sqlalchemy import update

    await db.execute(update(User).where(User.id == user_id).values(refresh_token_jti=None))
    await db.commit()


async def store_refresh_token_jti(db: AsyncSession, user_id: str, jti: str) -> None:
    """Store refresh token JTI in users table (invalidates previous refresh token).

    Args:
        db: Database session
        user_id: UUID of the user
        jti: JWT ID of the refresh token
    """
    from sqlalchemy import update

    await db.execute(update(User).where(User.id == user_id).values(refresh_token_jti=jti))
    await db.commit()


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_min))
    # jti (JWT ID) único por token – usado para revogação/blacklist
    to_encode.update({"exp": expire, "jti": str(uuid.uuid4()), "token_type": "access"})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any]) -> tuple[str, str]:
    """Create refresh token with 7-day expiration (sliding window).

    Args:
        data: Payload dict with user_id, tenant_id, role

    Returns:
        tuple[str, str]: (refresh_token, jti)
    """
    to_encode = data.copy()
    jti = str(uuid.uuid4())
    expire = datetime.now(timezone.utc) + timedelta(days=7)  # 7 days sliding window
    to_encode.update({"exp": expire, "jti": jti, "token_type": "refresh"})
    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, jti


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
        token_type = payload.get("token_type")
        if token_type and token_type != "access":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Verificar blacklist — token revogado via logout
    jti = payload.get("jti")
    if jti:
        try:
            r = await _get_auth_redis()
            if r and await r.exists(f"betaml:revoked:jti:{jti}"):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token revogado",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception:
            pass  # falha de Redis não bloqueia autenticação

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.active:
        raise credentials_exception
    return user


def require_roles(*roles: str) -> Callable[..., Any]:
    async def checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado para role '{current_user.role}'",
            )
        return current_user
    return checker


async def validate_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    """
    Valida o header X-API-Key comparando o SHA-256 contra a tabela api_keys.

    Efeitos colaterais após validação bem-sucedida:
    - Atualiza api_key.last_used_at no banco
    - Incrementa contador Redis `apikey_usage:{key_prefix}:{YYYY-MM-DD}` (TTL 32 dias)

    Levanta HTTP 401 se a chave for inválida, inativa ou expirada.
    """
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()

    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.active.is_(True),
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou inativa",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Verificar expiração
    if api_key.expires_at is not None:
        exp = api_key.expires_at
        # Normaliza para aware datetime para comparação segura
        if exp.tzinfo is None:
            exp_ts = exp.timestamp()
        else:
            exp_ts = exp.timestamp()
        if datetime.now(timezone.utc).timestamp() > exp_ts:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key expirada",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    # Atualizar last_used_at (best-effort — não bloqueia em caso de falha de commit)
    try:
        api_key.last_used_at = datetime.now(timezone.utc)
        await db.commit()
    except Exception:
        await db.rollback()

    # Incrementar contador de uso no Redis
    try:
        r = await _get_auth_redis()
        if r:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            counter_key = f"apikey_usage:{api_key.key_prefix}:{today}"
            await r.incr(counter_key)
            await r.expire(counter_key, 32 * 24 * 3600)  # 32 dias
    except Exception:
        pass  # falha de Redis não bloqueia autenticação

    return api_key


# ── Dual-auth principal for ingest endpoints ──────────────────────────────────

@dataclass
class IngestPrincipal:
    """Duck-typed auth principal for ingest endpoints (JWT user or API key)."""
    tenant_id: str
    id: str | None = None  # User.id for JWT auth; None for API key requests
    role: str = "API_KEY"


async def get_ingest_principal(
    authorization: str | None = Header(None, alias="Authorization"),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> IngestPrincipal:
    """Accept either X-API-Key header or Bearer JWT for ingest endpoints."""
    if x_api_key:
        ak = await validate_api_key(x_api_key=x_api_key, db=db)
        perms: list[str] = ak.permissions or ["ingest"]
        if "ingest" not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key não tem permissão 'ingest'",
            )
        return IngestPrincipal(tenant_id=ak.tenant_id, id=None, role="API_KEY")
    if authorization and authorization.startswith("Bearer "):
        user = await get_current_user(token=authorization[7:], db=db)
        if user.role not in ("ADMIN", "AML_ANALYST"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado para role '{user.role}'",
            )
        return IngestPrincipal(tenant_id=user.tenant_id, id=user.id, role=user.role)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: use Bearer token or X-API-Key header",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── PII Encryption (Fernet = AES-128-CBC + HMAC-SHA-256) ─────────────────────
# A chave Fernet é derivada do PII_ENCRYPTION_KEY via SHA-256 (normaliza para 32 bytes).
# Em produção, substitua por integração com AWS KMS ou HashiCorp Vault: a chave
# deve ser recuperada do KMS e nunca armazenada em variável de ambiente.
_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        raw_key = settings.pii_encryption_key.encode("utf-8")
        # SHA-256 sempre produz 32 bytes, compatível com Fernet (requer 32 bytes URL-safe base64)
        key_32 = hashlib.sha256(raw_key).digest()
        fernet_key = base64.urlsafe_b64encode(key_32)
        _fernet_instance = Fernet(fernet_key)
    return _fernet_instance


def encrypt_pii(plain: str) -> bytes:
    """Cifra dado PII (CPF, nome) com Fernet (AES-128-CBC + HMAC). Retorna bytes."""
    return _get_fernet().encrypt(plain.encode("utf-8"))


def decrypt_pii(ciphertext: bytes) -> str:
    """Decifra dado PII. Lânça ValueError em token inválido/corrompido."""
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("PII token inválido ou chave incorreta") from exc


def mask_cpf(cpf: str) -> str:
    """Exibe apenas os 3 últimos dígitos: ***.***.***-XX"""
    digits = "".join(c for c in cpf if c.isdigit())
    if len(digits) >= 3:
        return f"***.***.***.{digits[-2:]}"
    return "***"
