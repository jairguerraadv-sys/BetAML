"""JWT authentication + RBAC dependency."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import ApiKey, Tenant, User

logger = logging.getLogger(__name__)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ── Papéis da plataforma BetAML ───────────────────────────────────────────────

class AppRole(str):
    """Constantes de papéis (usa strings simples para serialização JSON fácil)."""
    ANALISTA      = "Operador_Analista"
    GESTOR        = "Operador_Gestor"
    ADMIN_TECNICO = "Operador_AdminTecnico"
    SUPER_ADMIN   = "BetAML_SuperAdmin"


# Papéis válidos (novo + legado para backward compat)
ROLES: frozenset[str] = frozenset({
    # Novos papéis semanticamente corretos
    AppRole.ANALISTA, AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN,
    # Legados — mantidos para compatibilidade com tokens existentes e seeds
    "SUPER_ADMIN", "ADMIN", "AML_ANALYST", "AUDITOR",
})

# Mapeamento legado → novos papéis (para usuários sem a coluna `roles` preenchida)
_LEGACY_ROLE_MAP: dict[str, list[str]] = {
    "AML_ANALYST": [AppRole.ANALISTA],
    "AUDITOR":     [AppRole.ANALISTA],                                    # auditor = acesso leitura = Analista
    "ADMIN":       [AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.ANALISTA],  # admin legacy = acumula funções
    "SUPER_ADMIN": [AppRole.SUPER_ADMIN],
}

# Mapa estático de permissões (resource:action) por papel
_PERMISSIONS: dict[str, frozenset[str]] = {
    AppRole.ANALISTA: frozenset({
        "alerts:read",   "alerts:write",
        "cases:read",    "cases:write",
        "players:read",  "players:write",
        "reports:read",  "reports:write",
        "notifications:read",
        "audit:read",
        "player_lists:read",
    }),
    AppRole.GESTOR: frozenset({
        # herda Analista
        "alerts:read",      "alerts:write",
        "cases:read",       "cases:write",   "cases:admin",
        "players:read",     "players:write",
        "reports:read",     "reports:write", "reports_kpi:read",
        "notifications:read",
        "audit:read",
        "player_lists:read", "player_lists:write",
        # exclusivo Gestor
        "sensitivity:read",  "sensitivity:write",
        "rules:read",        "rules:write",
    }),
    AppRole.ADMIN_TECNICO: frozenset({
        "mappings:read",       "mappings:write",
        "ingest:read",         "ingest:write",
        "ingest_errors:read",  "ingest_errors:write",
        "users:read",          "users:write",
        "audit:read",
        "settings:read",       "settings:write",
    }),
    AppRole.SUPER_ADMIN: frozenset({
        "tenants:read",   "tenants:write",  "tenants:admin",
        "templates:read", "templates:write",
        "ml_global:read", "ml_global:write",
        "platform_audit:read",
        "roles_global:admin",
        "users:read",     "users:write",
        "*",   # superpermissão
    }),
}


def get_effective_roles(user: Any) -> set[str]:
    """Retorna o conjunto de papéis efetivos de um usuário.

    Consulta a coluna `roles` (JSONB) se preenchida; caso contrário,
    deriva do campo legado `role` (string) via _LEGACY_ROLE_MAP.
    Inclui sempre o valor legado para compatibilidade com guards antigos.
    """
    # Novo estilo: coluna `roles` JSONB populada
    user_roles: list[str] | None = getattr(user, "roles", None)
    if user_roles:
        return set(user_roles)
    # Fallback legado
    legacy = getattr(user, "role", "")
    derived = set(_LEGACY_ROLE_MAP.get(legacy, [AppRole.ANALISTA]))
    derived.add(legacy)   # mantém nome legado para guards antigos que usam require_roles("ADMIN")
    return derived

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
    issued_at = datetime.now(timezone.utc)
    expire = issued_at + (expires_delta or timedelta(minutes=settings.access_token_expire_min))
    # jti (JWT ID) único por token – usado para revogação/blacklist
    to_encode.update({
        "exp": expire,
        "iat": issued_at,
        "nbf": issued_at,
        "jti": str(uuid.uuid4()),
        "token_type": "access",
    })
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
    issued_at = datetime.now(timezone.utc)
    expire = issued_at + timedelta(days=7)  # 7 days sliding window
    to_encode.update({
        "exp": expire,
        "iat": issued_at,
        "nbf": issued_at,
        "jti": jti,
        "token_type": "refresh",
    })
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
        if token_type != "access":
            raise credentials_exception
        user_id: str = payload.get("sub")
        token_tenant_id: str = str(payload.get("tenant_id") or "").strip()
        if not user_id or not token_tenant_id:
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
    if str(user.tenant_id) != token_tenant_id:
        logger.warning(
            "jwt_tenant_mismatch",
            extra={
                "token_tenant_id": token_tenant_id,
                "user_tenant_id": str(user.tenant_id),
                "user_id": str(user.id),
            },
        )
        raise credentials_exception
    return user


def require_roles(*roles: str) -> Callable[..., Any]:
    """Requer que o usuário possua pelo menos um dos papéis indicados.

    Aceita nomes de papéis legados ("ADMIN", "AML_ANALYST") e novos
    ("Operador_Gestor"), verificando contra get_effective_roles().
    """
    roles_set = set(roles)
    async def checker(current_user: User = Depends(get_current_user)) -> User:
        user_roles = get_effective_roles(current_user)
        if not user_roles.intersection(roles_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado. Papéis necessários: {sorted(roles_set)}",
            )
        return current_user
    return checker


def require_role(role: str) -> Callable[..., Any]:
    """Requer que o usuário possua exatamente este papel."""
    return require_roles(role)


def require_role_any(roles: list[str]) -> Callable[..., Any]:
    """Requer que o usuário possua pelo menos um dos papéis da lista."""
    return require_roles(*roles)


def require_permission(permission: str) -> Callable[..., Any]:
    """Requer permissão específica no formato 'resource:action'.

    Verifica o mapa _PERMISSIONS para todos os papéis efetivos do usuário.
    BetAML_SuperAdmin possui a superpermissão '*' que concede tudo.
    """
    async def checker(current_user: User = Depends(get_current_user)) -> User:
        user_roles = get_effective_roles(current_user)
        for r in user_roles:
            perms = _PERMISSIONS.get(r, frozenset())
            if "*" in perms or permission in perms:
                return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Acesso negado. Permissão necessária: {permission}",
        )
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

    def _tenant_from_raw_api_key(raw_key: str) -> str | None:
        # v2 format: btml_<tenant_uuid_hex32>_<secret>
        if not raw_key.startswith("btml_"):
            return None
        parts = raw_key.split("_", 2)
        if len(parts) < 3:
            return None
        tenant_hex = parts[1].strip().lower()
        if len(tenant_hex) != 32:
            return None
        try:
            return str(uuid.UUID(hex=tenant_hex))
        except ValueError:
            return None

    api_key: ApiKey | None = None
    hinted_tenant = _tenant_from_raw_api_key(x_api_key)

    # Fast path for v2 keys: set tenant context before querying api_keys (FORCE RLS).
    if hinted_tenant:
        await _set_db_tenant_context(db, hinted_tenant)
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.tenant_id == hinted_tenant,
                ApiKey.key_hash == key_hash,
                ApiKey.active.is_(True),
            )
        )
        api_key = result.scalar_one_or_none()

    # Backward-compatible path for legacy keys without tenant hint.
    if api_key is None and not hinted_tenant:
        import structlog as _structlog
        _structlog.get_logger(__name__).warning(
            "legacy_api_key_scan",
            msg=(
                "API key sem prefixo 'btml_' — usando scan O(N) em todos os tenants. "
                "Migre para o formato v2 (btml_<tenant_uuid_hex32>_<secret>) antes de 2026-12-31. "
                "Este path será removido na próxima versão major."
            ),
        )
        tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()
        for tenant_id in tenant_ids:
            await _set_db_tenant_context(db, str(tenant_id))
            result = await db.execute(
                select(ApiKey).where(
                    ApiKey.key_hash == key_hash,
                    ApiKey.active.is_(True),
                )
            )
            api_key = result.scalar_one_or_none()
            if api_key is not None:
                break

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


async def _set_db_tenant_context(db: AsyncSession, tenant_id: str) -> None:
    """Set the Postgres session variable used by RLS policies.

    Important: `get_db()` sets this based on ContextVar (JWT middleware path).
    For API key auth (and for direct unit tests calling dependencies), we must
    set it explicitly on the active DB session.
    """
    try:
        await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": tenant_id})
    except Exception:
        # Best-effort: if DB doesn't support set_config (e.g., sqlite tests), ignore.
        return


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

        # Ensure RLS tenant isolation applies for subsequent queries in the same request.
        await _set_db_tenant_context(db, str(ak.tenant_id))

        tenant = await db.get(Tenant, str(ak.tenant_id))
        if not tenant or not tenant.active:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Tenant is inactive")
        settings_json = tenant.settings or {}
        if isinstance(settings_json, dict) and settings_json.get("ingest_paused") is True:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ingest is paused")

        return IngestPrincipal(tenant_id=ak.tenant_id, id=None, role="API_KEY")
    if authorization and authorization.startswith("Bearer "):
        user = await get_current_user(token=authorization[7:], db=db)
        ingest_roles = {AppRole.ANALISTA, AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN,
                        "ADMIN", "AML_ANALYST"}  # legado incluído
        user_roles = get_effective_roles(user)
        if not user_roles.intersection(ingest_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso negado para role '{user.role}'",
            )

        # Defensive: helps when called outside request/middleware context.
        await _set_db_tenant_context(db, str(user.tenant_id))

        tenant = await db.get(Tenant, str(user.tenant_id))
        if not tenant or not tenant.active:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Tenant is inactive")
        settings_json = tenant.settings or {}
        if isinstance(settings_json, dict) and settings_json.get("ingest_paused") is True:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Ingest is paused")

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


def derive_pii_fernet_key(raw_secret: str) -> bytes:
    """Normaliza o secret de PII para um Fernet key estável de 32 bytes."""
    key_32 = hashlib.sha256(raw_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(key_32)


def derive_cpf_hmac_key(raw_secret: str) -> bytes:
    """Deriva chave HMAC separada por domínio para CPF."""
    return hashlib.sha256(raw_secret.encode("utf-8") + b":cpf_hmac").digest()


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(derive_pii_fernet_key(settings.pii_encryption_key))
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


# ── CPF HMAC para lookup indexado O(1) ───────────────────────────────────────
# HMAC-SHA256 determinístico do CPF (somente dígitos) com chave derivada da
# PII_ENCRYPTION_KEY mais um salt de domínio fixo ("cpf_hmac").
# Permite busca por CPF na tabela players sem descriptografia de toda a tabela.
# O HMAC não permite reversão do CPF original — privacidade mantida.

def compute_cpf_hmac(cpf_plain: str) -> str:
    """
    Computa HMAC-SHA256 do CPF (somente dígitos) para indexação.

    Returns:
        str — hex digest de 64 chars, indexável no banco (coluna cpf_hmac).
    """
    digits = "".join(c for c in cpf_plain if c.isdigit())
    hmac_key = derive_cpf_hmac_key(settings.pii_encryption_key)
    return hmac.new(hmac_key, digits.encode("utf-8"), hashlib.sha256).hexdigest()
