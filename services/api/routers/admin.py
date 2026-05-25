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
import os
import re
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import desc, func as sqlfunc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, create_access_token, get_current_user, hash_password, require_role, require_role_any
from config import settings
from database import get_db
from libs.models import (
    ApiKey,
    AuditLog,
    Case,
    IngestError,
    IngestJob,
    MappingConfig,
    ModelRegistry,
    RuleDefinition,
    RuleMacro,
    ScoringConfig,
    SystemFlag,
    Tenant,
    User,
)
from libs.schemas import (
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyOut,
    ApiKeyUsageOut,
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

# Roles assignable via admin endpoint (novos + legados para backward compat)
_ASSIGNABLE_ROLES = frozenset({
    # Novos papéis
    AppRole.ANALISTA, AppRole.GESTOR, AppRole.ADMIN_TECNICO,
    # Legados — aceitos durante período de migração
    "ADMIN", "AML_ANALYST", "AUDITOR",
})

# Mapeamento de role (legado ou novo) → lista de novos papéis para coluna `roles`
_LEGACY_TO_ROLES: dict[str, list[str]] = {
    "AML_ANALYST":      [AppRole.ANALISTA],
    "AUDITOR":          [],
    "ADMIN":            [AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.ANALISTA],
    AppRole.ANALISTA:   [AppRole.ANALISTA],
    AppRole.GESTOR:     [AppRole.GESTOR, AppRole.ANALISTA],
    AppRole.ADMIN_TECNICO: [AppRole.ADMIN_TECNICO],
}

def _role_to_roles_list(role: str) -> list[str]:
    """Converte campo `role` (legado ou novo) para lista de novos papéis."""
    return _LEGACY_TO_ROLES.get(role, [role])


def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


async def _set_current_tenant_context(db: AsyncSession, tenant_id: str | None) -> None:
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, false)"),
        {"tid": tenant_id or ""},
    )


def _normalize_source_system_alias(
    source_system: str,
    allowed_source_systems: set[str] | frozenset[str],
) -> str:
    value = (source_system or "").strip()
    if not value:
        return value
    if value in allowed_source_systems:
        return value

    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    canonical_map = {
        re.sub(r"[^a-z0-9]+", "", item.lower()): item
        for item in allowed_source_systems
    }
    alias_map = {
        "gamma": "ConnectorGamma",
        "delta": "ConnectorDelta",
        "epsilon": "ConnectorEpsilon",
        "connectorgamma": "ConnectorGamma",
        "connectordelta": "ConnectorDelta",
        "connectorepsilon": "ConnectorEpsilon",
    }
    return canonical_map.get(normalized) or alias_map.get(normalized) or value


async def _write_audit(db, tenant_id, actor, action, resource_type, resource_id=None, details=None):
    db.add(AuditLog(
        tenant_id=tenant_id, user_id=actor, action=action,
        entity_type=resource_type,
        entity_id=str(resource_id) if resource_id else None,
        after=details or {},
    ))


async def _require_target_tenant(
    db: AsyncSession,
    *,
    tenant_id: str,
) -> Tenant:
    await _set_current_tenant_context(db, None)
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant não encontrado")
    if not getattr(tenant, "active", True):
        raise HTTPException(409, "Tenant está inativo")
    return tenant


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


class AdminOnboardingMappingIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    source_system: str = Field(..., min_length=2, max_length=100)
    entity_type: str = Field(default="transaction", min_length=3, max_length=50)
    config_json: dict | None = None
    config_text: str | None = None
    format: str = Field(default="yaml", pattern="^(json|yaml)$")
    change_notes: str | None = None
    version: str = "1.0"


class AdminOnboardingRuleIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    description: Optional[str] = None
    status: str = "ACTIVE"
    severity: str = "MEDIUM"
    scope: str = "TRANSACTION"
    condition_dsl: str
    params: dict = Field(default_factory=dict)
    weight: float = 0.5


class AdminOnboardingImportOut(BaseModel):
    job_id: str
    status: str
    source_system: str
    file_name: Optional[str] = None


class ResetPasswordIn(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=256)


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


class OperationalAlertOut(BaseModel):
    code: str
    severity: str
    message: str
    value: float | int | None = None
    threshold: float | int | None = None


class DLQBreakdownOut(BaseModel):
    source_system: str
    entity_type: str | None = None
    count: int


class OpsSummaryOut(BaseModel):
    generated_at: datetime
    maintenance_mode: bool
    kafka_consumer_lag: int
    ingest_error_rate_24h_percent: float
    unresolved_dlq_events: int
    dlq_breakdown: list[DLQBreakdownOut]
    ingest_rate_limit_per_min: int
    ws_active_connections: int
    ws_queued_messages: int
    ws_peak_queue_depth: int
    ws_backpressure_events: int
    ws_last_backpressure_at: datetime | None = None
    stale_models: int
    oldest_model_age_days: int | None = None
    alerts: list[OperationalAlertOut] = Field(default_factory=list)


class AutoCasePolicyOut(BaseModel):
    auto_case_threshold: float
    severity_gates: dict[str, float]
    materializer: str
    legacy_alert_processor_enabled: bool
    legacy_alert_processor_allowed: bool


def _system_flag_payload(flag) -> dict:
    payload = getattr(flag, "flag_value", None)
    if not isinstance(payload, dict):
        payload = getattr(flag, "value", None)
    return payload if isinstance(payload, dict) else {}


async def _get_maintenance_enabled_for_ops(db: AsyncSession, tenant_id: str) -> bool:
    if type(db).__module__.startswith("unittest.mock"):
        flag = await db.get(SystemFlag, "maintenance_mode")
    else:
        flag = (
            await db.execute(
                select(SystemFlag).where(
                    SystemFlag.tenant_id == tenant_id,
                    SystemFlag.flag_name == "maintenance_mode",
                )
            )
        ).scalar_one_or_none()
    return bool(_system_flag_payload(flag).get("enabled", False)) if flag else False


# ── Maintenance mode ───────────────────────────────────────────────────────────

@router.post("/admin/maintenance-mode", tags=["admin"])
async def set_maintenance_mode(
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    """Toggle maintenance mode para o tenant (bloqueia ingest + scoring)."""
    flag = (
        await db.execute(
            select(SystemFlag).where(
                SystemFlag.tenant_id == current_user.tenant_id,
                SystemFlag.flag_name == "maintenance_mode",
            )
        )
    ).scalar_one_or_none()
    new_value = {"enabled": enabled}
    if flag is None:
        db.add(SystemFlag(
            tenant_id=current_user.tenant_id,
            flag_name="maintenance_mode",
            flag_value=new_value,
            updated_by=current_user.id,
        ))
    else:
        flag.flag_value = new_value
        flag.updated_by = current_user.id
        flag.updated_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "SET_MAINTENANCE_MODE", "SystemFlag", None, {"enabled": enabled})
    await db.commit()
    try:
        import time as _time
        from middleware import _CACHE_TTL, _maintenance_cache

        _maintenance_cache[str(current_user.tenant_id)] = (enabled, _time.monotonic() + _CACHE_TTL)
    except Exception:
        pass
    return {"maintenance_mode": enabled}


@router.put("/admin/maintenance-mode", tags=["admin"])
async def set_maintenance_mode_put(
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    """REST-friendly alias for maintenance mode toggle."""
    return await set_maintenance_mode(enabled=enabled, db=db, current_user=current_user)


@router.get("/admin/ops/summary", response_model=OpsSummaryOut, tags=["admin"])
async def get_ops_summary(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any([AppRole.GESTOR, AppRole.SUPER_ADMIN])),
):
    """Operational summary for infra dashboards and admin triage."""
    import httpx
    from routers.ingest import _ensure_ws_runtime, _tenant_ingest_rate_limit

    now_utc = datetime.now(UTC)
    since_24h = now_utc - timedelta(hours=24)
    tenant_id = current_user.tenant_id

    maintenance_enabled = await _get_maintenance_enabled_for_ops(db, tenant_id)

    unresolved_dlq = int((await db.execute(
        select(sqlfunc.count(IngestError.id)).where(
            IngestError.tenant_id == tenant_id,
            IngestError.resolved.is_(False),
        )
    )).scalar() or 0)
    dlq_breakdown_rows = (await db.execute(
        select(
            IngestError.source_system,
            IngestError.entity_type,
            sqlfunc.count(IngestError.id),
        ).where(
            IngestError.tenant_id == tenant_id,
            IngestError.resolved.is_(False),
        ).group_by(
            IngestError.source_system,
            IngestError.entity_type,
        ).order_by(
            sqlfunc.count(IngestError.id).desc()
        ).limit(5)
    )).all()

    ingest_rows = (await db.execute(
        select(
            sqlfunc.coalesce(sqlfunc.sum(IngestJob.processed_records), 0),
            sqlfunc.coalesce(sqlfunc.sum(IngestJob.failed_records), 0),
        ).where(
            IngestJob.tenant_id == tenant_id,
            IngestJob.created_at >= since_24h,
        )
    )).one()
    processed_24h = int(ingest_rows[0] or 0)
    failed_24h = int(ingest_rows[1] or 0)
    ingest_total = processed_24h + failed_24h
    ingest_error_rate = round((failed_24h / ingest_total) * 100.0, 2) if ingest_total else 0.0

    stale_cutoff = now_utc - timedelta(days=30)
    stale_models = int((await db.execute(
        select(sqlfunc.count(ModelRegistry.id)).where(
            ModelRegistry.tenant_id == tenant_id,
            ModelRegistry.status.in_(["PRODUCTION", "STAGING"]),
            ModelRegistry.trained_at.is_not(None),
            ModelRegistry.trained_at < stale_cutoff,
        )
    )).scalar() or 0)

    oldest_model_dt = (await db.execute(
        select(sqlfunc.min(ModelRegistry.trained_at)).where(
            ModelRegistry.tenant_id == tenant_id,
            ModelRegistry.status.in_(["PRODUCTION", "STAGING"]),
            ModelRegistry.trained_at.is_not(None),
        )
    )).scalar()
    oldest_model_age_days = (
        max(int((now_utc - oldest_model_dt).total_seconds() // 86400), 0)
        if oldest_model_dt is not None
        else None
    )

    kafka_lag = 0
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.redpanda_admin_url.rstrip('/')}/v1/consumer_groups")
        if resp.status_code == 200:
            groups = resp.json()
            if isinstance(groups, list) and groups:
                kafka_lag = max(int(group.get("lag", 0) or 0) for group in groups)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ops_summary_kafka_lag_failed", error=str(exc))

    ingest_rate_limit = await _tenant_ingest_rate_limit(db, str(tenant_id), default_limit=300)
    ws_runtime = _ensure_ws_runtime(str(tenant_id))
    ws_last_backpressure_at = None
    raw_last_backpressure = ws_runtime.get("last_backpressure_at")
    if isinstance(raw_last_backpressure, str):
        try:
            ws_last_backpressure_at = datetime.fromisoformat(raw_last_backpressure)
        except ValueError:
            ws_last_backpressure_at = None

    alerts: list[OperationalAlertOut] = []
    if kafka_lag > 1000:
        alerts.append(OperationalAlertOut(
            code="KAFKA_LAG_HIGH",
            severity="warning",
            message="Lag do consumer Kafka acima do threshold operacional.",
            value=kafka_lag,
            threshold=1000,
        ))
    if ingest_error_rate > 5.0:
        alerts.append(OperationalAlertOut(
            code="INGEST_ERROR_RATE_HIGH",
            severity="warning",
            message="Taxa de erros de ingestão nas últimas 24h acima do limite.",
            value=ingest_error_rate,
            threshold=5.0,
        ))
    if stale_models > 0:
        alerts.append(OperationalAlertOut(
            code="ML_MODEL_STALE",
            severity="warning",
            message="Há modelos de ML sem re-treino há mais de 30 dias.",
            value=stale_models,
            threshold=30,
        ))
    if unresolved_dlq > 0:
        alerts.append(OperationalAlertOut(
            code="DLQ_PENDING",
            severity="warning",
            message="Existem eventos pendentes na DLQ / quarentena.",
            value=unresolved_dlq,
            threshold=0,
        ))
    if int(ws_runtime.get("backpressure_events", 0) or 0) > 0:
        alerts.append(OperationalAlertOut(
            code="INGEST_WS_BACKPRESSURE",
            severity="warning",
            message="Canal WebSocket de ingestão registrou eventos de backpressure.",
            value=int(ws_runtime.get("backpressure_events", 0) or 0),
            threshold=0,
        ))

    return OpsSummaryOut(
        generated_at=now_utc,
        maintenance_mode=maintenance_enabled,
        kafka_consumer_lag=kafka_lag,
        ingest_error_rate_24h_percent=ingest_error_rate,
        unresolved_dlq_events=unresolved_dlq,
        dlq_breakdown=[
            DLQBreakdownOut(
                source_system=str(source_system),
                entity_type=str(entity_type) if entity_type is not None else None,
                count=int(count or 0),
            )
            for source_system, entity_type, count in dlq_breakdown_rows
        ],
        ingest_rate_limit_per_min=ingest_rate_limit,
        ws_active_connections=int(ws_runtime.get("active_connections", 0) or 0),
        ws_queued_messages=int(ws_runtime.get("queued_messages", 0) or 0),
        ws_peak_queue_depth=int(ws_runtime.get("peak_queue_depth", 0) or 0),
        ws_backpressure_events=int(ws_runtime.get("backpressure_events", 0) or 0),
        ws_last_backpressure_at=ws_last_backpressure_at,
        stale_models=stale_models,
        oldest_model_age_days=oldest_model_age_days,
        alerts=alerts,
    )


@router.get("/admin/kpis/aml", response_model=AMLKPIOut, tags=["admin"])
async def get_aml_kpis(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_role_any([AppRole.GESTOR, AppRole.SUPER_ADMIN])),
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
    current_user=Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    permissions = body.permissions or ["ingest"]
    tenant_compact = str(current_user.tenant_id).replace("-", "").lower()
    raw_key = f"btml_{tenant_compact}_{secrets.token_hex(24)}"
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()
    key = ApiKey(
        tenant_id=current_user.tenant_id,
        name=body.name,
        key_hash=hashed,
        key_prefix=raw_key[:8],
        source_system=body.source_system,
        permissions=permissions,  # ORM column: permissions
        active=True,              # ORM column: active
        expires_at=(
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days else None
        ),
    )
    db.add(key)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_API_KEY", "ApiKey", key.id, {
                           "name": body.name,
                           "source_system": body.source_system,
                           "permissions": permissions,
                           "expires_in_days": body.expires_in_days,
                       })
    await db.commit()
    return {
        **{c.name: getattr(key, c.name) for c in key.__table__.columns if c.name != "key_hash"},
        "raw_key": raw_key,
    }


@router.delete("/admin/api-keys/{key_id}", status_code=204, tags=["admin"])
async def revoke_api_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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


@router.get("/admin/api-keys/{key_id}/usage", response_model=ApiKeyUsageOut, tags=["admin"])
async def get_api_key_usage(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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

    return ApiKeyUsageOut(
        key_id=key_id,
        key_prefix=row.key_prefix,
        name=row.name,
        source_system=row.source_system,
        permissions=list(row.permissions or []),
        active=bool(row.active),
        last_used_at=row.last_used_at,
        total_requests_30d=sum(int(v or 0) for v in usage.values()),
        days=usage,
    )


# ── System Flags ───────────────────────────────────────────────────────────────

@router.get("/admin/flags", response_model=list[SystemFlagOut], tags=["admin"])
async def list_system_flags(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    result = await db.execute(
        select(SystemFlag).where(SystemFlag.tenant_id == current_user.tenant_id)
    )
    return result.scalars().all()


@router.put("/admin/flags/{flag_name}", response_model=SystemFlagOut, tags=["admin"])
async def upsert_system_flag(
    flag_name: str,
    body: SystemFlagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    flag = (
        await db.execute(
            select(SystemFlag).where(
                SystemFlag.tenant_id == current_user.tenant_id,
                SystemFlag.flag_name == flag_name,
            )
        )
    ).scalar_one_or_none()
    if flag is None:
        flag = SystemFlag(
            tenant_id=current_user.tenant_id,
            flag_name=flag_name,
            flag_value=body.value,
            updated_by=current_user.id,
        )
        db.add(flag)
    else:
        flag.flag_value = body.value
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
    current_user = Depends(require_role_any([AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    row = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)

    if body.ml_challenger_pct is not None and not (0 <= body.ml_challenger_pct <= 100):
        raise HTTPException(422, "ml_challenger_pct deve estar entre 0 e 100")
    if body.auto_case_threshold is not None and not (0 <= body.auto_case_threshold <= 1):
        raise HTTPException(422, "auto_case_threshold deve estar entre 0 e 1")
    if body.risk_band_low_threshold is not None and not (0 <= body.risk_band_low_threshold <= 1):
        raise HTTPException(422, "risk_band_low_threshold deve estar entre 0 e 1")
    if body.risk_band_high_threshold is not None and not (0 <= body.risk_band_high_threshold <= 1):
        raise HTTPException(422, "risk_band_high_threshold deve estar entre 0 e 1")
    low_band = body.risk_band_low_threshold if body.risk_band_low_threshold is not None else row.risk_band_low_threshold
    high_band = body.risk_band_high_threshold if body.risk_band_high_threshold is not None else row.risk_band_high_threshold
    if float(low_band) >= float(high_band):
        raise HTTPException(422, "risk_band_low_threshold deve ser menor que risk_band_high_threshold")
    if body.income_volume_ratio_threshold is not None and body.income_volume_ratio_threshold <= 0:
        raise HTTPException(422, "income_volume_ratio_threshold deve ser maior que zero")

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
    current_user = Depends(require_role_any([AppRole.GESTOR, AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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


@router.get("/auto-case-policy", response_model=AutoCasePolicyOut, tags=["admin"])
async def get_auto_case_policy(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Expõe o contrato operacional da política de auto-case."""
    row = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "ScoringConfig não encontrada. Execute seeds.")

    legacy_enabled = os.getenv("ALERT_PROCESSOR_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    legacy_allowed = settings.environment in {"development", "test"}
    return AutoCasePolicyOut(
        auto_case_threshold=float(row.auto_case_threshold),
        severity_gates={
            "CRITICAL": float(row.critical_threshold),
            "HIGH": float(row.high_threshold),
            "MEDIUM": float(row.medium_threshold),
            "LOW": float(row.low_threshold),
        },
        materializer="rules_engine",
        legacy_alert_processor_enabled=legacy_enabled,
        legacy_alert_processor_allowed=legacy_allowed,
    )


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
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """
    Onboarding de novo tenant via API.

    Cria automaticamente:
    - Tenant com slug único
    - Usuário ADMIN inicial
    - ScoringConfig com thresholds padrão

    Requer role ADMIN (multi-tenant hierarchy — ADMIN de qualquer tenant pode criar tenants em dev).
    Em produção, considere restringir para SUPER_ADMIN dedicado.
    """
    await _set_current_tenant_context(db, None)

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
    await _set_current_tenant_context(db, tenant.id)

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
        ml_challenger_pct=0,
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
    await db.flush()

    await _set_current_tenant_context(db, current_user.tenant_id)
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
            f"ScoringConfig provisionada e tenant pronto para concluir o wizard."
        ),
    )


@router.post("/admin/onboarding/{tenant_id}/mappings", status_code=201, tags=["admin"])
async def create_onboarding_mapping(
    tenant_id: str,
    body: AdminOnboardingMappingIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """Creates the first mapping for a newly onboarded tenant without switching session context."""
    from routers.mappings import _next_version_number, _parse_config_payload
    from libs.mapping import validate_mapping_targets_against_canonical_schema
    from sqlalchemy import update
    from routers.ingest import ALLOWED_SOURCE_SYSTEMS

    tenant = await _require_target_tenant(db, tenant_id=tenant_id)
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, false)"),
        {"tid": str(tenant.id)},
    )
    cfg = _parse_config_payload(
        config_json=body.config_json,
        config_text=body.config_text,
        fmt=body.format,
    )
    canonical_validation = validate_mapping_targets_against_canonical_schema(cfg)
    if not canonical_validation["valid"]:
        raise HTTPException(
            422,
            {
                "message": "Config incompatível com schema canônico de ingestão",
                "canonical_validation": canonical_validation,
            },
        )

    source_system = _normalize_source_system_alias(body.source_system, ALLOWED_SOURCE_SYSTEMS)
    entity_type = body.entity_type.upper()
    version_number = await _next_version_number(
        db,
        str(tenant.id),
        source_system,
        entity_type,
    )

    await db.execute(
        update(MappingConfig)
        .where(
            MappingConfig.tenant_id == str(tenant.id),
            MappingConfig.source_system == source_system,
            MappingConfig.entity_type == entity_type,
        )
        .values(is_current=False)
    )

    mapping = MappingConfig(
        tenant_id=str(tenant.id),
        name=body.name,
        source_system=source_system,
        entity_type=entity_type,
        config_json=cfg,
        version=body.version,
        version_number=version_number,
        is_current=True,
        change_notes=body.change_notes or "Criado via onboarding wizard",
        created_by=current_user.id,
    )
    db.add(mapping)
    await db.flush()
    await _write_audit(
        db,
        str(tenant.id),
        current_user.id,
        "CREATE_MAPPING",
        "MappingConfig",
        mapping.id,
        {
            "name": mapping.name,
            "source_system": mapping.source_system,
            "entity_type": mapping.entity_type,
            "version_number": mapping.version_number,
            "via": "admin_onboarding",
        },
    )
    await db.commit()
    await db.refresh(mapping)
    return {
        "id": mapping.id,
        "name": mapping.name,
        "version_number": mapping.version_number,
        "is_current": mapping.is_current,
    }


@router.post("/admin/onboarding/{tenant_id}/ingest-sample", response_model=AdminOnboardingImportOut, status_code=202, tags=["admin"])
async def ingest_onboarding_sample(
    tenant_id: str,
    file: UploadFile = File(...),
    source_system: str = Form(...),
    mapping_config_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """Queues a sample ingest job for the newly created tenant."""
    from routers.ingest import ALLOWED_SOURCE_SYSTEMS, _publish_with_retries, _upload_bronze_file
    from utils import get_producer

    tenant = await _require_target_tenant(db, tenant_id=tenant_id)
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, false)"),
        {"tid": str(tenant.id)},
    )
    source_system = _normalize_source_system_alias(source_system, ALLOWED_SOURCE_SYSTEMS)
    if source_system not in ALLOWED_SOURCE_SYSTEMS:
        raise HTTPException(400, f"source_system '{source_system}' não reconhecido. Permitidos: {sorted(ALLOWED_SOURCE_SYSTEMS)}")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Arquivo de teste vazio")

    mapping_version_id = None
    if mapping_config_id:
        mapping = await db.get(MappingConfig, mapping_config_id)
        if not mapping or mapping.tenant_id != str(tenant.id):
            raise HTTPException(404, "MappingConfig não encontrado para o tenant")
        if not mapping.is_current:
            mapping_version_id = mapping.id

    job = IngestJob(
        tenant_id=str(tenant.id),
        source_system=source_system,
        mapping_config_id=mapping_config_id,
        mapping_version_id=mapping_version_id,
        file_name=file.filename,
        file_size_bytes=len(content),
        file_path=None,
        bytes_processed=0,
        status="QUEUED",
        created_by=current_user.id,
    )
    db.add(job)
    await db.flush()

    bronze_path = _upload_bronze_file(
        tenant_id=str(tenant.id),
        job_id=str(job.id),
        file_name=file.filename or "sample.csv",
        content=content,
    )
    if bronze_path:
        job.file_path = bronze_path

    producer = await get_producer()
    if producer:
        ok = await _publish_with_retries(
            producer=producer,
            topic="ingest.jobs",
            payload={
                "job_id": job.id,
                "tenant_id": str(tenant.id),
                "source_system": source_system,
                "mapping_config_id": mapping_config_id,
                "mapping_version_id": mapping_version_id,
                "file_name": file.filename,
                "file_path": job.file_path,
            },
            key=str(job.id),
            tenant_id=str(tenant.id),
            source_system=source_system,
            context={"endpoint": "/admin/onboarding/ingest-sample", "job_id": str(job.id)},
        )
        if not ok:
            raise HTTPException(503, "Falha ao enfileirar amostra de ingestão; enviado para DLQ")

    await _write_audit(
        db,
        str(tenant.id),
        current_user.id,
        "CREATE_INGEST_SAMPLE_JOB",
        "IngestJob",
        job.id,
        {
            "source_system": source_system,
            "file_name": file.filename,
            "mapping_config_id": mapping_config_id,
            "via": "admin_onboarding",
        },
    )
    await db.commit()
    return AdminOnboardingImportOut(
        job_id=str(job.id),
        status=str(job.status),
        source_system=source_system,
        file_name=file.filename,
    )


@router.post("/admin/onboarding/{tenant_id}/rules", status_code=201, tags=["admin"])
async def create_onboarding_rule(
    tenant_id: str,
    body: AdminOnboardingRuleIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """Creates the first rule for a newly onboarded tenant without requiring a separate login."""
    from libs.dsl_parser import validate_dsl

    tenant = await _require_target_tenant(db, tenant_id=tenant_id)
    await db.execute(
        text("SELECT set_config('app.current_tenant', :tid, false)"),
        {"tid": str(tenant.id)},
    )
    macros = {
        row.name: row.expression
        for row in (
            await db.execute(
                select(RuleMacro).where(RuleMacro.tenant_id == str(tenant.id))
            )
        ).scalars().all()
    }
    ok, msg = validate_dsl(body.condition_dsl, macros=macros)
    if not ok:
        raise HTTPException(400, detail=f"DSL inválido: {msg}")

    rule = RuleDefinition(
        tenant_id=str(tenant.id),
        name=body.name,
        description=body.description,
        status=body.status,
        severity=body.severity,
        scope=body.scope,
        condition_dsl=body.condition_dsl,
        params=body.params,
        weight=body.weight,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await _write_audit(
        db,
        str(tenant.id),
        current_user.id,
        "CREATE",
        "RuleDefinition",
        rule.id,
        {
            **body.model_dump(),
            "via": "admin_onboarding",
        },
    )
    await db.commit()
    return {"id": rule.id, "name": rule.name, "status": rule.status}


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
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """Lista todos os tenants da plataforma (SUPER_ADMIN only)."""
    await _set_current_tenant_context(db, None)
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
    current_user = Depends(require_role(AppRole.SUPER_ADMIN)),
):
    """Atualiza nome ou status ativo do tenant (SUPER_ADMIN only)."""
    await _set_current_tenant_context(db, None)
    t = await db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "Tenant não encontrado")
    await _set_current_tenant_context(db, tenant_id)
    if body.name is not None:
        t.name = body.name
    if body.active is not None:
        t.active = body.active
    await db.flush()
    await _set_current_tenant_context(db, current_user.tenant_id)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "UPDATE_TENANT", "Tenant", tenant_id,
                       {"name": body.name, "active": body.active})
    await db.commit()
    await _set_current_tenant_context(db, None)
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
        roles=_role_to_roles_list(body.role),
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
        target.roles = _role_to_roles_list(body.role)
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
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
    body: ResetPasswordIn,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
):
    """
    Redefine a senha do usuário com valor informado pelo operador autorizado.
    Não retorna senha em plaintext para evitar vazamento em logs/telemetria.
    """
    target = (await db.execute(
        select(User).where(
            User.id == user_id,
            _tenant_filter(User, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Usuário não encontrado")

    target.password_hash = hash_password(body.new_password)

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
        "message": "Senha redefinida com sucesso.",
    }


@router.post("/admin/invite", tags=["admin"])
async def generate_invite(
    body: InviteIn,
    current_user = Depends(require_role_any([AppRole.ADMIN_TECNICO, AppRole.SUPER_ADMIN])),
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
        # Usa fragmento para evitar expor token em logs de proxy/servidor.
        "invite_link": f"/accept-invite#token={token}",
        "email": body.email,
        "role": body.role,
        "expires_in_hours": 48,
    }
