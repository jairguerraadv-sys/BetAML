"""
routers/admin.py — Endpoints administrativos do tenant:
  - Maintenance mode
  - API Keys CRUD + usage stats
  - System Flags CRUD
  - Scoring Config
  - POST /admin/tenants (onboarding de novo tenant via API)
  - User management (GET/POST/PATCH/DELETE /admin/users, reset-password, invite)
"""
from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, get_current_user, hash_password, require_roles
from config import settings
from database import get_db
from libs.models import (
    ApiKey,
    AuditLog,
    Case,
    IngestJob,
    RuleDefinition,
    ScoringConfig,
    SystemFlag,
    Tenant,
    User,
)
from libs.schemas import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyOut,
    InviteIn,
    ScoringConfigOut,
    ScoringConfigUpdate,
    ScoringPreviewIn,
    ScoringPreviewOut,
    SystemFlagOut,
    SystemFlagUpdate,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
    PreviewBandCount,
)
from libs.models import Alert

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["admin"])

# Roles that non-SUPER_ADMIN admins are permitted to assign
_ASSIGNABLE_ROLES = frozenset({"ADMIN", "AML_ANALYST", "AUDITOR"})


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


async def _write_audit(db, tenant_id, actor, action, resource_type, resource_id=None, details=None):
    db.add(AuditLog(
        tenant_id=tenant_id, user_id=actor, action=action,
        entity_type=resource_type,
        entity_id=str(resource_id) if resource_id else None,
        after=details or {},
    ))


# ── Schemas locais ─────────────────────────────────────────────────────────────

class TenantCreateIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9_-]+$")
    admin_username: str = Field(..., min_length=3, max_length=50)
    admin_email: str
    admin_password: str = Field(..., min_length=8)
    risk_score_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    cnpj: Optional[str] = Field(default=None, pattern=r"^\d{14}$", description="14 dígitos sem formatação")


class TenantCreateOut(BaseModel):
    tenant_id: str
    slug: str
    admin_user_id: str
    admin_username: str
    message: str


class AMLKPIOut(BaseModel):
    generated_at: datetime
    window_days: int
    alerts_open: int
    alerts_in_review: int
    alerts_labeled_30d: int
    true_positive_rate_30d_percent: float
    false_positive_rate_30d_percent: float
    cases_open: int
    cases_overdue: int
    sla_breach_rate_open_cases_percent: float
    avg_case_resolution_hours_30d: float


# ── Maintenance mode ───────────────────────────────────────────────────────────

@router.post("/admin/maintenance-mode", tags=["admin"])
async def set_maintenance_mode(
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """Toggle maintenance mode para o tenant (bloqueia ingest + scoring)."""
    flag_key = f"{current_user.tenant_id}:maintenance_mode"
    flag = await db.get(SystemFlag, flag_key)
    new_value = {"enabled": enabled}
    if flag is None:
        db.add(SystemFlag(
            key=flag_key,
            value=new_value,
            updated_by=current_user.id,
        ))
    else:
        flag.value = new_value
        flag.updated_by = current_user.id
        flag.updated_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "SET_MAINTENANCE_MODE", "SystemFlag", None, {"enabled": enabled})
    await db.commit()
    return {"maintenance_mode": enabled}


@router.get("/admin/kpis/aml", response_model=AMLKPIOut, tags=["admin"])
async def get_aml_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("ADMIN", "AUDITOR", "AML_ANALYST")),
):
    """Scorecard AML operacional para triagem, qualidade de rotulagem e SLA de casos."""
    now_utc = datetime.now(UTC)
    since_30d = now_utc - timedelta(days=30)
    tenant_id = current_user.tenant_id

    alerts_open = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tenant_id,
            Alert.status == "OPEN",
        )
    )).scalar() or 0)

    alerts_in_review = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tenant_id,
            Alert.status == "IN_REVIEW",
        )
    )).scalar() or 0)

    alerts_labeled_30d = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tenant_id,
            Alert.labeled_at.is_not(None),
            Alert.labeled_at >= since_30d,
        )
    )).scalar() or 0)

    true_positive_30d = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tenant_id,
            Alert.label == "TRUE_POSITIVE",
            Alert.labeled_at.is_not(None),
            Alert.labeled_at >= since_30d,
        )
    )).scalar() or 0)

    false_positive_30d = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tenant_id,
            Alert.label == "FALSE_POSITIVE",
            Alert.labeled_at.is_not(None),
            Alert.labeled_at >= since_30d,
        )
    )).scalar() or 0)

    cases_open = int((await db.execute(
        select(sqlfunc.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.status.in_(["OPEN", "IN_REVIEW"]),
        )
    )).scalar() or 0)

    cases_overdue = int((await db.execute(
        select(sqlfunc.count(Case.id)).where(
            Case.tenant_id == tenant_id,
            Case.status.in_(["OPEN", "IN_REVIEW"]),
            Case.sla_due_at.is_not(None),
            Case.sla_due_at < now_utc,
        )
    )).scalar() or 0)

    avg_case_resolution_hours_30d = float((await db.execute(
        select(
            sqlfunc.coalesce(
                sqlfunc.avg(sqlfunc.extract("epoch", Case.closed_at - Case.created_at) / 3600.0),
                0.0,
            )
        ).where(
            Case.tenant_id == tenant_id,
            Case.closed_at.is_not(None),
            Case.closed_at >= since_30d,
        )
    )).scalar() or 0.0)

    labeled_den = max(alerts_labeled_30d, 1)
    open_cases_den = max(cases_open, 1)

    return AMLKPIOut(
        generated_at=now_utc,
        window_days=30,
        alerts_open=alerts_open,
        alerts_in_review=alerts_in_review,
        alerts_labeled_30d=alerts_labeled_30d,
        true_positive_rate_30d_percent=round((true_positive_30d / labeled_den) * 100.0, 2) if alerts_labeled_30d else 0.0,
        false_positive_rate_30d_percent=round((false_positive_30d / labeled_den) * 100.0, 2) if alerts_labeled_30d else 0.0,
        cases_open=cases_open,
        cases_overdue=cases_overdue,
        sla_breach_rate_open_cases_percent=round((cases_overdue / open_cases_den) * 100.0, 2) if cases_open else 0.0,
        avg_case_resolution_hours_30d=round(avg_case_resolution_hours_30d, 2),
    )


# ── Usage Stats ────────────────────────────────────────────────────────────────

@router.get("/admin/stats/usage", tags=["admin"])
async def get_usage_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_roles("ADMIN")),
):
    """Retorna métricas de uso do tenant para o mês corrente."""
    from sqlalchemy import text as _text

    tid = current_user.tenant_id
    first_of_month = date.today().replace(day=1)

    events_this_month = int((await db.execute(
        select(sqlfunc.coalesce(sqlfunc.sum(IngestJob.processed_records), 0)).where(
            IngestJob.tenant_id == tid,
            IngestJob.created_at >= first_of_month,
        )
    )).scalar() or 0)

    alerts_this_month = int((await db.execute(
        select(sqlfunc.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.created_at >= first_of_month,
        )
    )).scalar() or 0)

    open_cases = int((await db.execute(
        select(sqlfunc.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.not_in(["CLOSED", "REPORTED"]),
        )
    )).scalar() or 0)

    # DB size — best-effort, fallback 0
    db_size_mb = 0.0
    try:
        result = await db.execute(_text("SELECT pg_database_size(current_database())"))
        db_size_mb = round((result.scalar() or 0) / (1024 * 1024), 2)
    except Exception:
        pass

    # MinIO storage — best-effort, fallback 0
    minio_mb = 0.0
    try:
        from minio import Minio

        mc = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https"),
        )
        total_bytes = sum(
            obj.size
            for obj in mc.list_objects(settings.minio_bucket, prefix=f"{tid}/", recursive=True)
            if obj.size
        )
        minio_mb = round(total_bytes / (1024 * 1024), 2)
    except Exception:
        pass

    return {
        "tenant_id": tid,
        "period": str(first_of_month),
        "events_this_month": events_this_month,
        "alerts_this_month": alerts_this_month,
        "open_cases": open_cases,
        "db_size_mb": db_size_mb,
        "minio_mb": minio_mb,
    }


# ── API Keys ───────────────────────────────────────────────────────────────────

@router.get("/admin/api-keys", response_model=list[ApiKeyOut], tags=["admin"])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    result = await db.execute(
        select(ApiKey).where(_tenant_filter(ApiKey, current_user.tenant_id))
        .order_by(desc(ApiKey.created_at))
    )
    return result.scalars().all()


@router.post("/admin/api-keys", response_model=ApiKeyCreateResponse, status_code=201, tags=["admin"])
async def create_api_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    raw_key = "btml_" + secrets.token_hex(32)
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()
    key = ApiKey(
        tenant_id=current_user.tenant_id,
        name=body.name,
        key_hash=hashed,
        key_prefix=raw_key[:8],
        permissions=body.permissions,  # ORM column: permissions
        active=True,              # ORM column: active
        expires_at=(
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days else None
        ),
    )
    db.add(key)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_API_KEY", "ApiKey", key.id, {"name": body.name})
    await db.commit()
    return {
        **{c.name: getattr(key, c.name) for c in key.__table__.columns if c.name != "key_hash"},
        "raw_key": raw_key,
    }


@router.delete("/admin/api-keys/{key_id}", status_code=204, tags=["admin"])
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    row = (await db.execute(
        select(ApiKey).where(ApiKey.id == key_id,
                             _tenant_filter(ApiKey, current_user.tenant_id))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    row.active = False  # ORM column: active
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "REVOKE_API_KEY", "ApiKey", key_id)
    await db.commit()


@router.get("/admin/api-keys/{key_id}/usage", tags=["admin"])
async def get_api_key_usage(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """Retorna os últimos 30 dias de contadores de uso diário da API key (via Redis)."""
    from datetime import date, timedelta as _td

    row = (await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            _tenant_filter(ApiKey, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "API key not found")

    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        today = date.today()
        usage: dict[str, int] = {}
        for i in range(30):
            d = (today - _td(days=i)).strftime("%Y-%m-%d")
            counter_key = f"apikey_usage:{row.key_prefix}:{d}"
            count = await r.get(counter_key)
            usage[d] = int(count) if count else 0
        await r.aclose()
    except Exception as exc:
        logger.warning("api_key_usage_redis_error", error=str(exc))
        usage = {}

    return {
        "key_id": key_id,
        "key_prefix": row.key_prefix,
        "name": row.name,
        "last_used_at": row.last_used_at,
        "days": usage,
    }


# ── System Flags ───────────────────────────────────────────────────────────────

@router.get("/admin/flags", response_model=list[SystemFlagOut], tags=["admin"])
async def list_system_flags(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    # Keys are stored as "{tenant_id}:{flag_name}"
    prefix = f"{current_user.tenant_id}:%"
    result = await db.execute(
        select(SystemFlag).where(SystemFlag.key.like(prefix))
    )
    return result.scalars().all()


@router.put("/admin/flags/{flag_name}", response_model=SystemFlagOut, tags=["admin"])
async def upsert_system_flag(
    flag_name: str,
    body: SystemFlagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    flag_key = f"{current_user.tenant_id}:{flag_name}"
    flag = await db.get(SystemFlag, flag_key)
    if flag is None:
        flag = SystemFlag(key=flag_key, value=body.value, updated_by=current_user.id)
        db.add(flag)
    else:
        flag.value = body.value
        flag.updated_by = current_user.id
        flag.updated_at = datetime.now(UTC)
    await db.commit()
    return flag


# ── Scoring Config ─────────────────────────────────────────────────────────────

@router.get("/scoring-config", response_model=ScoringConfigOut, tags=["admin"])
async def get_scoring_config(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    row = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "ScoringConfig não encontrada. Execute seeds.")
    return row


@router.put("/scoring-config", response_model=ScoringConfigOut, tags=["admin"])
async def update_scoring_config(
    body: ScoringConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    row = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(row, field, val)
    row.updated_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "UPDATE_SCORING_CONFIG", "ScoringConfig", row.id,
                       body.model_dump(exclude_none=True))
    await db.commit()
    return row


@router.post("/scoring-config/preview", response_model=ScoringPreviewOut, tags=["admin"])
async def preview_scoring_config(
    body: ScoringPreviewIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """Simula quantos alertas teriam sido gerados nos últimos 30d com a config proposta."""
    from datetime import timedelta

    current_cfg = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id)
    )).scalar_one_or_none()
    if current_cfg is None:
        raise HTTPException(404, "ScoringConfig não encontrada")

    proposed = ScoringPreviewIn(
        low_threshold=body.low_threshold if body.low_threshold is not None else current_cfg.low_threshold,
        medium_threshold=body.medium_threshold if body.medium_threshold is not None else current_cfg.medium_threshold,
        high_threshold=body.high_threshold if body.high_threshold is not None else current_cfg.high_threshold,
        critical_threshold=body.critical_threshold if body.critical_threshold is not None else current_cfg.critical_threshold,
    )
    since = datetime.now(UTC) - timedelta(days=30)
    result = await db.execute(
        select(Alert.anomaly_score, Alert.severity).where(
            Alert.tenant_id == current_user.tenant_id,
            Alert.created_at >= since,
            Alert.anomaly_score.isnot(None),
        ).limit(5000)
    )
    rows = result.all()
    total = len(rows)

    def _bucket(score: float, cfg: ScoringPreviewIn) -> str | None:
        s = score * 100
        if s >= (cfg.critical_threshold or 95):
            return "critical"
        if s >= (cfg.high_threshold or 80):
            return "high"
        if s >= (cfg.medium_threshold or 60):
            return "medium"
        if s >= (cfg.low_threshold or 30):
            return "low"
        return None

    cur = PreviewBandCount()
    prop = PreviewBandCount()
    for row_score, row_sev in rows:
        fs = float(row_score) if row_score is not None else None
        if fs is None:
            continue
        sev = (row_sev or "").upper()
        if sev == "CRITICAL":
            cur.critical += 1
        elif sev == "HIGH":
            cur.high += 1
        elif sev == "MEDIUM":
            cur.medium += 1
        elif sev == "LOW":
            cur.low += 1
        b = _bucket(fs, proposed)
        if b == "critical":
            prop.critical += 1
        elif b == "high":
            prop.high += 1
        elif b == "medium":
            prop.medium += 1
        elif b == "low":
            prop.low += 1

    return ScoringPreviewOut(current=cur, proposed=prop, total_alerts_30d=total)


# ── Tenant Onboarding ──────────────────────────────────────────────────────────

DEFAULT_RULES_TEMPLATE = [
    {
        "name": "Structuring (Muitos depósitos pequenos 24h)",
        "severity": "HIGH", "scope": "TRANSACTION",
        "condition_dsl": 'features.deposit_count_24h >= params.count_threshold and features.deposit_sum_24h >= params.sum_threshold and transaction.type == "DEPOSIT"',
        "params": {"count_threshold": 5, "sum_threshold": 5000},
    },
    {
        "name": "PEP com depósito acima do threshold",
        "severity": "CRITICAL", "scope": "TRANSACTION",
        "condition_dsl": 'player.pep_flag == true and transaction.amount >= params.pep_threshold',
        "params": {"pep_threshold": 5000},
    },
    {
        "name": "Round-tripping (depósito → aposta mínima → saque)",
        "severity": "CRITICAL", "scope": "TRANSACTION",
        "condition_dsl": 'transaction.type == "WITHDRAWAL" and ratio(features.withdrawal_sum_24h, features.deposit_sum_24h) >= params.round_trip_ratio and features.bet_stake_sum_24h <= params.max_stake',
        "params": {"round_trip_ratio": "0.8", "max_stake": 50},
    },
    {
        "name": "Spike vs Baseline (Z-Score)",
        "severity": "HIGH", "scope": "TRANSACTION",
        "condition_dsl": 'zscore(features.deposit_sum_24h, features.baseline_avg_daily_deposit, features.baseline_stddev_deposit) >= params.zscore_threshold and transaction.type == "DEPOSIT"',
        "params": {"zscore_threshold": 3},
    },
    {
        "name": "Conta bancária compartilhada",
        "severity": "HIGH", "scope": "TRANSACTION",
        "condition_dsl": "features.shared_bank_account_count >= params.shared_threshold",
        "params": {"shared_threshold": 2},
    },
]


@router.post("/admin/tenants", response_model=TenantCreateOut, status_code=201, tags=["admin"])
async def create_tenant(
    body: TenantCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("SUPER_ADMIN")),
):
    """
    Onboarding de novo tenant via API.

    Cria automaticamente:
    - Tenant com slug único
    - Usuário ADMIN inicial
    - ScoringConfig com thresholds padrão
    - 5 regras DSL de partida

    Requer role ADMIN (multi-tenant hierarchy — ADMIN de qualquer tenant pode criar tenants em dev).
    Em produção, considere restringir para SUPER_ADMIN dedicado.
    """
    # Verificar unicidade do slug
    existing = (await db.execute(
        select(Tenant).where(Tenant.slug == body.slug)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Slug '{body.slug}' já está em uso por outro tenant.")

    # Criar tenant
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=body.name,
        slug=body.slug,
        active=True,
        settings={"cnpj": body.cnpj} if body.cnpj else {},
        risk_score_threshold=body.risk_score_threshold,
    )
    db.add(tenant)
    await db.flush()

    # Criar usuário ADMIN inicial
    admin_user = User(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        username=body.admin_username,
        email=body.admin_email,
        password_hash=hash_password(body.admin_password),
        role="ADMIN",
        active=True,
    )
    db.add(admin_user)
    await db.flush()

    # ScoringConfig padrão
    scoring_cfg = ScoringConfig(
        tenant_id=tenant.id,
        rule_weight=0.4,
        ml_weight=0.4,
        network_weight=0.2,
        auto_case_threshold=0.75,
        risk_band_low_threshold=0.35,
        risk_band_high_threshold=0.70,
        income_volume_ratio_threshold=1.5,
        sla_critical_hours=4,
        sla_high_hours=24,
        sla_medium_hours=72,
        sla_low_hours=168,
        data_retention_days=730,
        updated_by=admin_user.id,
    )
    db.add(scoring_cfg)

    # Regras DSL padrão
    for rd in DEFAULT_RULES_TEMPLATE:
        db.add(RuleDefinition(
            tenant_id=tenant.id,
            name=rd["name"],
            status="ACTIVE",
            severity=rd["severity"],
            scope=rd["scope"],
            condition_dsl=rd["condition_dsl"],
            params=rd["params"],
            created_by=admin_user.id,
        ))

    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_TENANT", "Tenant", tenant.id,
                       {"slug": body.slug, "admin_username": body.admin_username})
    await db.commit()

    logger.info("tenant_created_via_api", tenant_id=tenant.id, slug=tenant.slug,
                admin_user_id=admin_user.id, by_user=current_user.id)

    return TenantCreateOut(
        tenant_id=tenant.id,
        slug=tenant.slug,
        admin_user_id=admin_user.id,
        admin_username=admin_user.username,
        message=(
            f"Tenant '{tenant.name}' criado com sucesso. "
            f"Login: {admin_user.username} / (senha fornecida). "
            f"5 regras DSL padrão ativas. ScoringConfig provisionada."
        ),
    )


# ── Tenant management (SUPER_ADMIN) ────────────────────────────────────────

class TenantOut(BaseModel):
    id: str
    name: str
    slug: str
    active: bool
    created_at: datetime
    user_count: Optional[int] = None

class TenantUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    active: Optional[bool] = None

@router.get("/admin/tenants", response_model=list[TenantOut], tags=["admin"])
async def list_tenants(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("SUPER_ADMIN")),
):
    """Lista todos os tenants da plataforma (SUPER_ADMIN only)."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    tenants = result.scalars().all()
    out = []
    for t in tenants:
        count_result = await db.execute(
            select(sqlfunc.count(User.id)).where(User.tenant_id == t.id)
        )
        out.append(TenantOut(
            id=str(t.id),
            name=t.name,
            slug=t.slug,
            active=getattr(t, "active", True),
            created_at=t.created_at,
            user_count=count_result.scalar_one(),
        ))
    return out


@router.patch("/admin/tenants/{tenant_id}", response_model=TenantOut, tags=["admin"])
async def update_tenant(
    tenant_id: str,
    body: TenantUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("SUPER_ADMIN")),
):
    """Atualiza nome ou status ativo do tenant (SUPER_ADMIN only)."""
    t = await db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Tenant não encontrado")
    if body.name is not None:
        t.name = body.name
    if body.active is not None:
        t.active = body.active
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "UPDATE_TENANT", "Tenant", tenant_id,
                       {"name": body.name, "active": body.active})
    await db.commit()
    await db.refresh(t)
    count_result = await db.execute(
        select(sqlfunc.count(User.id)).where(User.tenant_id == t.id)
    )
    return TenantOut(
        id=str(t.id),
        name=t.name,
        slug=t.slug,
        active=getattr(t, "active", True),
        created_at=t.created_at,
        user_count=count_result.scalar_one(),
    )


# ── User Management (ADMIN, tenant-scoped) ──────────────────────────────────

@router.get("/admin/users", response_model=list[UserOut], tags=["admin"])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """Lista todos os usuários do tenant atual. Senha não é exposta."""
    result = await db.execute(
        select(User)
        .where(_tenant_filter(User, current_user.tenant_id))
        .order_by(User.created_at.asc())
    )
    return result.scalars().all()


@router.post("/admin/users", response_model=UserOut, status_code=201, tags=["admin"])
async def create_user(
    body: UserCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """
    Cria um novo usuário no tenant.

    Restrições:
    - role não pode ser SUPER_ADMIN
    - username deve ser único no tenant
    """
    if body.role not in _ASSIGNABLE_ROLES:
        raise HTTPException(
            422,
            f"Role inválida. Permitidas: {sorted(_ASSIGNABLE_ROLES)}. "
            "SUPER_ADMIN não pode ser atribuído via este endpoint."
        )

    # Verificar unicidade de username dentro do tenant
    existing = (await db.execute(
        select(User).where(
            User.tenant_id == current_user.tenant_id,
            User.username == body.username,
        )
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Username '{body.username}' já existe neste tenant.")

    new_user = User(
        id=str(uuid.uuid4()),
        tenant_id=current_user.tenant_id,
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        active=True,
    )
    db.add(new_user)
    await db.flush()

    await _write_audit(
        db, current_user.tenant_id, current_user.id,
        "CREATE_USER", "User", new_user.id,
        {"username": body.username, "email": body.email, "role": body.role},
    )
    await db.commit()
    await db.refresh(new_user)

    logger.info("user_created", user_id=new_user.id, username=new_user.username,
                role=new_user.role, by=current_user.id)
    return new_user


@router.patch("/admin/users/{user_id}", response_model=UserOut, tags=["admin"])
async def update_user(
    user_id: str,
    body: UserUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """
    Atualiza role ou status ativo de um usuário.

    Restrições:
    - Não pode editar o próprio role
    - role não pode ser SUPER_ADMIN
    """
    target = (await db.execute(
        select(User).where(
            User.id == user_id,
            _tenant_filter(User, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Usuário não encontrado")

    if body.role is not None and user_id == current_user.id:
        raise HTTPException(422, "Não é possível editar o próprio role.")

    if body.role is not None and body.role not in _ASSIGNABLE_ROLES:
        raise HTTPException(
            422,
            f"Role inválida. Permitidas: {sorted(_ASSIGNABLE_ROLES)}."
        )

    before = {"role": target.role, "active": target.active}

    if body.role is not None:
        target.role = body.role
    if body.active is not None:
        target.active = body.active

    after = {"role": target.role, "active": target.active}

    db.add(AuditLog(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        action="UPDATE_USER",
        entity_type="User",
        entity_id=user_id,
        before=before,
        after=after,
    ))
    await db.commit()
    await db.refresh(target)

    logger.info("user_updated", user_id=user_id, before=before, after=after, by=current_user.id)
    return target


@router.delete("/admin/users/{user_id}", status_code=204, tags=["admin"])
async def deactivate_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """
    Desativa (soft-delete) um usuário. Não realiza deleção física.

    Restrição: não pode desativar a própria conta.
    """
    if user_id == current_user.id:
        raise HTTPException(422, "Não é possível desativar a própria conta.")

    target = (await db.execute(
        select(User).where(
            User.id == user_id,
            _tenant_filter(User, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Usuário não encontrado")

    target.active = False

    await _write_audit(
        db, current_user.tenant_id, current_user.id,
        "DEACTIVATE_USER", "User", user_id,
        {"username": target.username, "active": False},
    )
    await db.commit()
    logger.info("user_deactivated", user_id=user_id, by=current_user.id)


@router.post("/admin/users/{user_id}/reset-password", tags=["admin"])
async def reset_user_password(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN")),
):
    """
    Gera uma nova senha aleatória (16 chars) para o usuário e a salva como hash.
    Retorna a senha em plaintext UMA ÚNICA VEZ — não é possível recuperá-la depois.
    Sem envio de e-mail em MVP.
    """
    target = (await db.execute(
        select(User).where(
            User.id == user_id,
            _tenant_filter(User, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Usuário não encontrado")

    # Gera senha aleatória segura de 16 caracteres (letras + dígitos + símbolos)
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    new_password = "".join(secrets.choice(alphabet) for _ in range(16))
    target.password_hash = hash_password(new_password)

    await _write_audit(
        db, current_user.tenant_id, current_user.id,
        "RESET_USER_PASSWORD", "User", user_id,
        {"username": target.username, "reset_by": current_user.username},
    )
    await db.commit()

    logger.info("user_password_reset", user_id=user_id, by=current_user.id)
    return {
        "user_id": user_id,
        "username": target.username,
        "new_password": new_password,
        "message": "Senha redefinida. Guarde-a agora — não será exibida novamente.",
    }


@router.post("/admin/invite", tags=["admin"])
async def generate_invite(
    body: InviteIn,
    current_user = Depends(require_roles("ADMIN")),
):
    """
    Gera um token de convite JWT (48h de validade) contendo tenant_id, email e role.
    Retorna o link de aceite com o token embutido.
    Sem envio de e-mail em MVP — o link deve ser compartilhado manualmente.
    """
    if body.role not in _ASSIGNABLE_ROLES:
        raise HTTPException(
            422,
            f"Role inválida. Permitidas: {sorted(_ASSIGNABLE_ROLES)}."
        )

    token = create_access_token(
        data={
            "type": "invite",
            "tenant_id": current_user.tenant_id,
            "email": body.email,
            "role": body.role,
        },
        expires_delta=timedelta(hours=48),
    )

    logger.info(
        "invite_generated",
        email=body.email,
        role=body.role,
        tenant_id=current_user.tenant_id,
        by=current_user.id,
    )
    return {
        "invite_link": f"/accept-invite?token={token}",
        "email": body.email,
        "role": body.role,
        "expires_in_hours": 48,
    }
