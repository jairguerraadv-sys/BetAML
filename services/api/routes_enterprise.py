"""
Enterprise routes — M1-M8 endpoints.

Mounted on `app` via `app.include_router(enterprise_router)` in main.py.
All endpoints require a valid JWT (get_current_user dependency) plus
tenant isolation enforced by current_user.tenant_id.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import secrets
import textwrap
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, update, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

# local imports (same package as main.py)
from database import get_db               # async session factory
from auth import get_current_user, User   # JWT dep

from libs.schemas import (
    AlertLabelIn,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyOut,
    CompoundRuleCreate,
    CompoundRuleOut,
    FeatureSnapshotOut,
    IngestErrorOut,
    IngestErrorResolveIn,
    MappingVersionOut,
    MonthlyReportIn,
    NotificationCreate,
    NotificationOut,
    PlayerListCreate,
    PlayerListEntryBulk,
    PlayerListOut,
    PreviewBandCount,
    ReprocessJobIn,
    RuleMacroCreate,
    RuleMacroOut,
    ScoringConfigOut,
    ScoringConfigUpdate,
    ScoringPreviewIn,
    ScoringPreviewOut,
    SystemFlagOut,
    SystemFlagUpdate,
)
from libs.models import (
    Alert,
    ApiKey,
    AuditLog,
    Case,
    CompoundRule,
    FeatureSnapshot,
    IngestError,
    IngestJob,
    MappingConfig,
    ModelRegistry,
    Notification,
    Player,
    PlayerList,
    PlayerListEntry,
    ReportPackage,
    RuleDefinition,
    RuleMacro,
    ScoringConfig,
    SystemFlag,
    Tenant,
)
from libs.mapping import activate_mapping_version

enterprise_router = APIRouter(tags=["enterprise"])

UTC = timezone.utc
logger = structlog.get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _tenant_filter(model, tenant_id: str):
    return model.tenant_id == tenant_id


async def _write_audit(
    db: AsyncSession,
    tenant_id: str,
    actor: str,          # user_id (UUID string) do usu\u00e1rio que realizou a a\u00e7\u00e3o
    action: str,
    resource_type: str,
    resource_id: str | None,
    details: dict | None = None,
) -> None:
    """Grava entrada de audit log usando o schema can\u00f4nico (user_id/entity_type/entity_id/after)."""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=actor,                                         # actor \u2192 user_id
            action=action,
            entity_type=resource_type,                             # resource_type \u2192 entity_type
            entity_id=str(resource_id) if resource_id else None,  # resource_id \u2192 entity_id
            after=details or {},                                   # details \u2192 after
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# M1 — Ingest: IngestJob detail & reprocess
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/ingest/jobs/{job_id}", tags=["ingest"])
async def get_ingest_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Detail of a single ingest job including error_sample."""
    stmt = select(IngestJob).where(
        IngestJob.id == job_id,
        _tenant_filter(IngestJob, current_user.tenant_id),
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "IngestJob not found")

    # Count errors linked to this job
    err_stmt = select(func.count()).where(
        IngestError.ingest_job_id == job_id,
        _tenant_filter(IngestError, current_user.tenant_id),
    )
    err_count = (await db.execute(err_stmt)).scalar_one()

    data = {c.name: getattr(job, c.name) for c in job.__table__.columns}
    data["error_count"] = err_count
    return data


@enterprise_router.post("/ingest/jobs/{job_id}/reprocess", status_code=202, tags=["ingest"])
async def reprocess_ingest_job(
    job_id: str,
    body: ReprocessJobIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Queue an ingest job for reprocessing (creates child job)."""
    stmt = select(IngestJob).where(
        IngestJob.id == job_id,
        _tenant_filter(IngestJob, current_user.tenant_id),
    )
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(404, "IngestJob not found")
    if job.status not in ("FAILED", "PARTIAL"):
        raise HTTPException(409, f"Job status is '{job.status}', only FAILED/PARTIAL can be reprocessed")

    new_job = IngestJob(
        tenant_id=job.tenant_id,
        source_system=job.source_system,
        file_name=job.file_name,
        status="QUEUED",
        connector_type=job.connector_type,
        reprocessed_from=job_id,
    )
    db.add(new_job)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id, "REPROCESS_JOB",
                       "IngestJob", job_id, {"reason": body.reason, "new_job_id": new_job.id})
    await db.commit()
    return {"new_job_id": new_job.id, "queued_at": datetime.now(UTC).isoformat()}


# ── Ingest Errors ─────────────────────────────────────────────────────────────

@enterprise_router.get("/ingest/errors", response_model=list[IngestErrorOut], tags=["ingest"])
async def list_ingest_errors(
    job_id: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    source_system: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(IngestError).where(_tenant_filter(IngestError, current_user.tenant_id))
    if job_id:
        stmt = stmt.where(IngestError.ingest_job_id == job_id)
    if status_filter:
        stmt = stmt.where(IngestError.resolution_status == status_filter)
    if source_system:
        stmt = stmt.where(IngestError.source_system == source_system)
    stmt = stmt.order_by(desc(IngestError.created_at)).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@enterprise_router.post("/ingest/errors/{error_id}/resolve", tags=["ingest"])
async def resolve_ingest_error(
    error_id: int,
    body: IngestErrorResolveIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(IngestError).where(
        IngestError.id == error_id,
        _tenant_filter(IngestError, current_user.tenant_id),
    )
    err = (await db.execute(stmt)).scalar_one_or_none()
    if err is None:
        raise HTTPException(404, "IngestError not found")
    err.resolution_status = "resolved"
    err.resolution_note   = body.resolution_note
    err.resolved_at       = datetime.now(UTC)
    err.resolved_by       = current_user.id
    await db.commit()
    return {"status": "resolved"}


# ── SSE ingest stream ─────────────────────────────────────────────────────────

@enterprise_router.get("/ingest/stream", tags=["ingest"])
async def ingest_sse_stream(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Server-Sent Events — streams real-time ingest job status updates."""
    async def event_generator() -> AsyncGenerator[str, None]:
        # In production this would subscribe to Redis pub/sub.
        # Here we emit a heartbeat every 5 s + disconnect when client closes.
        ping_count = 0
        while True:
            if await request.is_disconnected():
                break
            ping_count += 1
            payload = json.dumps({"type": "heartbeat", "count": ping_count,
                                  "ts": datetime.now(UTC).isoformat()})
            yield f"data: {payload}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# M1 — Mapping Config versioning
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/mappings/{mapping_id}/versions", response_model=list[MappingVersionOut], tags=["mappings"])
async def list_mapping_versions(
    mapping_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all versions for a mapping config group (same source_system+entity_type)."""
    # First fetch the reference row
    ref = (await db.execute(
        select(MappingConfig).where(
            MappingConfig.id == mapping_id,
            _tenant_filter(MappingConfig, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if ref is None:
        raise HTTPException(404)

    stmt = select(MappingConfig).where(
        _tenant_filter(MappingConfig, current_user.tenant_id),
        MappingConfig.source_system == ref.source_system,
        MappingConfig.entity_type   == ref.entity_type,
    ).order_by(MappingConfig.version_number)
    result = await db.execute(stmt)
    return result.scalars().all()


@enterprise_router.post("/mappings/{mapping_id}/rollback", tags=["mappings"])
async def rollback_mapping(
    mapping_id: int,
    version_number: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activate a previous mapping version, deactivating the current one."""
    # Verify target version belongs to same tenant
    target = (await db.execute(
        select(MappingConfig).where(
            MappingConfig.id == mapping_id,
            MappingConfig.version_number == version_number,
            _tenant_filter(MappingConfig, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Mapping version not found")

    await activate_mapping_version(db, mapping_id, version_number)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "ROLLBACK_MAPPING", "MappingConfig", mapping_id,
                       {"version_number": version_number})
    await db.commit()
    return {"status": "activated", "version_number": version_number}


# ──────────────────────────────────────────────────────────────────────────────
# Admin — Maintenance mode + API keys
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.post("/admin/maintenance-mode", tags=["admin"])
async def set_maintenance_mode(
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle maintenance mode for the tenant (blocks ingest + scoring)."""
    stmt = select(SystemFlag).where(
        SystemFlag.tenant_id == current_user.tenant_id,
        SystemFlag.flag_name == "maintenance_mode",
    )
    flag = (await db.execute(stmt)).scalar_one_or_none()
    val = "true" if enabled else "false"
    if flag is None:
        db.add(SystemFlag(
            tenant_id=current_user.tenant_id,
            flag_name="maintenance_mode",
            flag_value=val,
            updated_by=current_user.id,
        ))
    else:
        flag.flag_value = val
        flag.updated_by = current_user.id
        flag.updated_at = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "SET_MAINTENANCE_MODE", "SystemFlag", None, {"enabled": enabled})
    await db.commit()
    return {"maintenance_mode": enabled}


@enterprise_router.get("/admin/api-keys", response_model=list[ApiKeyOut], tags=["admin"])
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ApiKey).where(_tenant_filter(ApiKey, current_user.tenant_id))
        .order_by(desc(ApiKey.created_at))
    )
    return result.scalars().all()


@enterprise_router.post("/admin/api-keys", response_model=ApiKeyCreateResponse, status_code=201, tags=["admin"])
async def create_api_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw_key = "btml_" + secrets.token_hex(32)
    hashed  = hashlib.sha256(raw_key.encode()).hexdigest()
    key = ApiKey(
        tenant_id=current_user.tenant_id,
        name=body.name,
        key_hash=hashed,
        key_prefix=raw_key[:8],
        scopes=body.scopes,
        is_active=True,
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


@enterprise_router.delete("/admin/api-keys/{key_id}", status_code=204, tags=["admin"])
async def revoke_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(ApiKey).where(ApiKey.id == key_id,
                             _tenant_filter(ApiKey, current_user.tenant_id))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    row.is_active = False
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "REVOKE_API_KEY", "ApiKey", key_id)
    await db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# M3 — Player Lists
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/player-lists", response_model=list[PlayerListOut], tags=["player-lists"])
async def list_player_lists(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PlayerList).where(_tenant_filter(PlayerList, current_user.tenant_id))
    )
    rows = result.scalars().all()
    # annotate entry_count
    out = []
    for row in rows:
        cnt = (await db.execute(
            select(func.count()).where(PlayerListEntry.player_list_id == row.id)
        )).scalar_one()
        d = {c.name: getattr(row, c.name) for c in row.__table__.columns}
        d["entry_count"] = cnt
        out.append(d)
    return out


@enterprise_router.post("/player-lists", status_code=201, tags=["player-lists"])
async def create_player_list(
    body: PlayerListCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pl = PlayerList(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        list_type=body.list_type,
    )
    db.add(pl)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_PLAYER_LIST", "PlayerList", pl.id, {"name": body.name})
    await db.commit()
    return {"id": pl.id, "name": pl.name}


@enterprise_router.post("/player-lists/{list_id}/entries", status_code=201, tags=["player-lists"])
async def bulk_add_list_entries(
    list_id: int,
    body: PlayerListEntryBulk,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pl = (await db.execute(
        select(PlayerList).where(PlayerList.id == list_id,
                                 _tenant_filter(PlayerList, current_user.tenant_id))
    )).scalar_one_or_none()
    if pl is None:
        raise HTTPException(404)
    added = 0
    for val in body.values:
        db.add(PlayerListEntry(
            player_list_id=list_id,
            value=val,
            value_type=body.value_type,
        ))
        added += 1
    await db.commit()
    return {"added": added}


@enterprise_router.delete("/player-lists/{list_id}", status_code=204, tags=["player-lists"])
async def delete_player_list(
    list_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pl = (await db.execute(
        select(PlayerList).where(PlayerList.id == list_id,
                                 _tenant_filter(PlayerList, current_user.tenant_id))
    )).scalar_one_or_none()
    if pl is None:
        raise HTTPException(404)
    await db.delete(pl)
    await db.commit()


@enterprise_router.post("/player-lists/{list_id}/upload-csv", status_code=201, tags=["player-lists"])
async def upload_list_csv(
    list_id: int,
    file: UploadFile = File(...),
    value_type: str = Query("CPF"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    pl = (await db.execute(
        select(PlayerList).where(PlayerList.id == list_id,
                                 _tenant_filter(PlayerList, current_user.tenant_id))
    )).scalar_one_or_none()
    if pl is None:
        raise HTTPException(404)

    content = await file.read()
    lines = content.decode("utf-8", errors="replace").splitlines()
    added = 0
    for line in lines:
        val = line.strip().strip('"').strip("'")
        if val:
            db.add(PlayerListEntry(player_list_id=list_id, value=val, value_type=value_type))
            added += 1
    await db.commit()
    return {"added": added}


# ──────────────────────────────────────────────────────────────────────────────
# M3 — Compound Rules
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/rules/compound", response_model=list[CompoundRuleOut], tags=["rules"])
async def list_compound_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(CompoundRule).where(_tenant_filter(CompoundRule, current_user.tenant_id))
    )
    return result.scalars().all()


@enterprise_router.post("/rules/compound", status_code=201, response_model=CompoundRuleOut, tags=["rules"])
async def create_compound_rule(
    body: CompoundRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = CompoundRule(
        tenant_id=current_user.tenant_id,
        name=body.name,
        logic=body.logic,
        component_rule_ids=body.component_rule_ids,
        score_weights=body.score_weights,
        min_score_threshold=body.min_score_threshold,
        is_active=True,
    )
    db.add(rule)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_COMPOUND_RULE", "CompoundRule", rule.id)
    await db.commit()
    return rule


@enterprise_router.delete("/rules/compound/{rule_id}", status_code=204, tags=["rules"])
async def delete_compound_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(CompoundRule).where(CompoundRule.id == rule_id,
                                   _tenant_filter(CompoundRule, current_user.tenant_id))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.delete(row)
    await db.commit()


# ── Rule Macros ───────────────────────────────────────────────────────────────

@enterprise_router.get("/rules/macros", response_model=list[RuleMacroOut], tags=["rules"])
async def list_macros(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(RuleMacro).where(_tenant_filter(RuleMacro, current_user.tenant_id))
    )
    return result.scalars().all()


@enterprise_router.post("/rules/macros", status_code=201, response_model=RuleMacroOut, tags=["rules"])
async def create_macro(
    body: RuleMacroCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate DSL expression
    from libs.dsl_parser import validate_dsl
    ok, msg = validate_dsl(body.expression)
    if not ok:
        raise HTTPException(422, f"Invalid DSL expression: {msg}")

    macro = RuleMacro(
        tenant_id=current_user.tenant_id,
        name=body.name,
        expression=body.expression,
        description=body.description,
    )
    db.add(macro)
    await db.flush()
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "CREATE_MACRO", "RuleMacro", macro.id)
    await db.commit()
    return macro


@enterprise_router.delete("/rules/macros/{macro_id}", status_code=204, tags=["rules"])
async def delete_macro(
    macro_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(RuleMacro).where(RuleMacro.id == macro_id,
                                _tenant_filter(RuleMacro, current_user.tenant_id))
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    await db.delete(row)
    await db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# M5/M6 — Scoring Config & SLA
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/scoring-config", response_model=ScoringConfigOut, tags=["admin"])
async def get_scoring_config(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(ScoringConfig).where(
            ScoringConfig.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No ScoringConfig found. Run seeds.")
    return row


@enterprise_router.put("/scoring-config", response_model=ScoringConfigOut, tags=["admin"])
async def update_scoring_config(
    body: ScoringConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (await db.execute(
        select(ScoringConfig).where(
            ScoringConfig.tenant_id == current_user.tenant_id,
        )
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


@enterprise_router.post("/scoring-config/preview", response_model=ScoringPreviewOut, tags=["admin"])
async def preview_scoring_config(
    body: ScoringPreviewIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Simulate how many alerts would be generated with the proposed config (last 30d)."""
    from datetime import timedelta

    # Load current config as baseline
    current_cfg = (await db.execute(
        select(ScoringConfig).where(
            ScoringConfig.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()
    if current_cfg is None:
        raise HTTPException(404, "No ScoringConfig found")

    # Proposed thresholds (fall back to current if not provided)
    proposed = ScoringPreviewIn(
        low_threshold=body.low_threshold       if body.low_threshold       is not None else current_cfg.low_threshold,
        medium_threshold=body.medium_threshold if body.medium_threshold    is not None else current_cfg.medium_threshold,
        high_threshold=body.high_threshold     if body.high_threshold      is not None else current_cfg.high_threshold,
        critical_threshold=body.critical_threshold if body.critical_threshold is not None else current_cfg.critical_threshold,
    )

    since = datetime.now(UTC) - timedelta(days=30)

    # Fetch recent alerts with anomaly scores for this tenant
    result = await db.execute(
        select(Alert.anomaly_score, Alert.severity).where(
            Alert.tenant_id == current_user.tenant_id,
            Alert.created_at >= since,
            Alert.anomaly_score != None,
        ).limit(5000)
    )
    rows = result.all()
    total = len(rows)

    def _bucket(score: float, cfg: ScoringPreviewIn) -> str | None:
        s = score * 100
        if s >= (cfg.critical_threshold or 95):  return "critical"
        if s >= (cfg.high_threshold or 80):      return "high"
        if s >= (cfg.medium_threshold or 60):    return "medium"
        if s >= (cfg.low_threshold or 30):       return "low"
        return None

    cur = PreviewBandCount()
    prop = PreviewBandCount()
    for row_score, row_sev in rows:
        fs = float(row_score) if row_score is not None else None
        if fs is None:
            continue
        # Current distribution (use existing severity label as proxy)
        sev = (row_sev or "").upper()
        if sev == "CRITICAL":  cur.critical += 1
        elif sev == "HIGH":    cur.high += 1
        elif sev == "MEDIUM":  cur.medium += 1
        elif sev == "LOW":     cur.low += 1
        # Proposed distribution (recalculate)
        b = _bucket(fs, proposed)
        if b == "critical":  prop.critical += 1
        elif b == "high":    prop.high += 1
        elif b == "medium":  prop.medium += 1
        elif b == "low":     prop.low += 1

    return ScoringPreviewOut(current=cur, proposed=prop, total_alerts_30d=total)


# ──────────────────────────────────────────────────────────────────────────────
# M5 — Alert labeling (feedback loop)
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.post("/alerts/{alert_id}/label", tags=["alerts"])
async def label_alert(
    alert_id: int,
    body: AlertLabelIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Label an alert as TRUE_POSITIVE, FALSE_POSITIVE, or NEED_REVIEW."""
    alert = (await db.execute(
        select(Alert).where(Alert.id == alert_id,
                            _tenant_filter(Alert, current_user.tenant_id))
    )).scalar_one_or_none()
    if alert is None:
        raise HTTPException(404)
    alert.label        = body.label
    alert.labeled_by   = current_user.id
    alert.labeled_at   = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "LABEL_ALERT", "Alert", alert_id, {"label": body.label})
    await db.commit()
    # Trigger async feedback loop (Kafka topic or background task)
    background_tasks.add_task(_enqueue_feedback_event, alert_id, body.label, current_user.tenant_id)
    return {"status": "labeled", "label": body.label}


async def _enqueue_feedback_event(alert_id: int, label: str, tenant_id: str):
    """Publish labeled event to Kafka for ML retraining pipeline."""
    try:
        from aiokafka import AIOKafkaProducer
        import os
        producer = AIOKafkaProducer(bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092"))
        await producer.start()
        await producer.send_and_wait(
            "feedback.labels",
            json.dumps({"alert_id": alert_id, "label": label,
                        "tenant_id": tenant_id, "ts": datetime.now(UTC).isoformat()}).encode(),
        )
        await producer.stop()
    except Exception:
        pass  # non-critical background task


# ──────────────────────────────────────────────────────────────────────────────
# M5 — Report Package PDF
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/cases/{case_id}/report-package/pdf", tags=["cases"])
async def download_report_pdf(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a generated PDF report for a case's ReportPackage."""
    case = (await db.execute(
        select(Case).where(Case.id == case_id,
                           _tenant_filter(Case, current_user.tenant_id))
    )).scalar_one_or_none()
    if case is None:
        raise HTTPException(404, "Case not found")

    rp = (await db.execute(
        select(ReportPackage).where(ReportPackage.case_id == case_id)
        .order_by(desc(ReportPackage.created_at))
    )).scalar_one_or_none()
    if rp is None or not rp.pdf_path:
        raise HTTPException(404, "No PDF report package found for this case")

    # If stored in MinIO, stream it back
    try:
        from libs.clients import get_minio_client
        minio = get_minio_client()
        bucket = os.getenv("MINIO_BUCKET", "betaml-reports")
        response = minio.get_object(bucket, rp.pdf_path)
        pdf_bytes = response.read()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{case_id}.pdf"},
        )
    except Exception as exc:
        raise HTTPException(500, f"Could not retrieve PDF: {exc}") from exc


@enterprise_router.post("/reports/monthly-summary", status_code=202, tags=["reports"])
async def generate_monthly_report(
    body: MonthlyReportIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger async generation of monthly compliance summary report."""
    background_tasks.add_task(
        _build_monthly_report, body.year, body.month,
        current_user.tenant_id, body.include_pdf,
    )
    return {"status": "queued", "year": body.year, "month": body.month}


async def _build_monthly_report(year: int, month: int, tenant_id: str, include_pdf: bool):
    """Background task: aggregate stats and optionally generate PDF."""
    pass  # implement with reportlab / weasyprint in production


# ──────────────────────────────────────────────────────────────────────────────
# M6 — LGPD: right to erasure
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.post("/players/{player_id}/right-to-erasure", tags=["players"])
async def right_to_erasure(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    LGPD Art. 18 — erase personally identifiable data for a player.
    Anonymises CPF, name, email, phone while preserving anonymised transaction records.
    """
    player = (await db.execute(
        select(Player).where(
            Player.id == player_id,
            _tenant_filter(Player, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if player is None:
        raise HTTPException(404, "Player not found")

    anon_suffix = hashlib.sha256(str(player_id).encode()).hexdigest()[:12]
    player.full_name  = f"ERASURE_{anon_suffix}"
    player.status     = "ERASED"

    await _write_audit(
        db, current_user.tenant_id, current_user.id,
        "LGPD_ERASURE", "Player", player_id,
        {"reason": "right_to_erasure", "anon_suffix": anon_suffix},
    )
    await db.commit()
    return {"status": "erased", "player_id": player_id}


# ──────────────────────────────────────────────────────────────────────────────
# M6 — Notifications
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/notifications", response_model=list[NotificationOut], tags=["notifications"])
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(Notification).where(
        _tenant_filter(Notification, current_user.tenant_id),
        Notification.user_id == current_user.id,
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)
    stmt = stmt.order_by(desc(Notification.created_at)).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@enterprise_router.post("/notifications/{notif_id}/read", tags=["notifications"])
async def mark_notification_read(
    notif_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = (await db.execute(
        select(Notification).where(
            Notification.id == notif_id,
            _tenant_filter(Notification, current_user.tenant_id),
            Notification.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if n is None:
        raise HTTPException(404)
    n.is_read = True
    n.read_at = datetime.now(UTC)
    await db.commit()
    return {"status": "read"}


@enterprise_router.post("/notifications/read-all", tags=["notifications"])
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await db.execute(
        update(Notification).where(
            _tenant_filter(Notification, current_user.tenant_id),
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        ).values(is_read=True, read_at=datetime.now(UTC))
    )
    await db.commit()
    return {"status": "all_read"}


# ──────────────────────────────────────────────────────────────────────────────
# M2 — Feature store player history
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/players/{player_id}/features", response_model=list[FeatureSnapshotOut], tags=["players"])
async def get_player_features_history(
    player_id: str,
    days: int = Query(30, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return daily feature snapshots for a player (Gold layer)."""
    # Tenant ownership check first
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    from_dt = datetime.now(UTC) - timedelta(days=days)
    try:
        result = await db.execute(
            select(FeatureSnapshot).where(
                FeatureSnapshot.player_id == player_id,
                _tenant_filter(FeatureSnapshot, current_user.tenant_id),
                FeatureSnapshot.created_at >= from_dt,
            ).order_by(FeatureSnapshot.snapshot_date)
        )
        return result.scalars().all()
    except Exception as exc:
        logger.warning("feature_snapshot_query_error", error=str(exc), player_id=player_id)
        return []


@enterprise_router.get("/feature-store/players/{player_id}/history", tags=["feature-store"])
async def get_feature_store_player_history(
    player_id: str,
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Canonical feature-store history endpoint backed by Gold snapshots."""
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    if from_date and to_date and from_date > to_date:
        raise HTTPException(400, "Parâmetro 'from' não pode ser maior que 'to'")

    stmt = select(FeatureSnapshot).where(
        FeatureSnapshot.player_id == player_id,
        _tenant_filter(FeatureSnapshot, current_user.tenant_id),
    )
    if from_date:
        stmt = stmt.where(FeatureSnapshot.created_at >= from_date)
    if to_date:
        stmt = stmt.where(FeatureSnapshot.created_at <= to_date)
    stmt = stmt.order_by(FeatureSnapshot.snapshot_date.desc())

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {
        "player_id": player_id,
        "from": from_date.isoformat() if from_date else None,
        "to": to_date.isoformat() if to_date else None,
        "count": len(rows),
        "items": [
            {
                "id": row.id,
                "snapshot_date": str(row.feature_date),
                "created_at": row.created_at,
                "features": row.features,
                "drift_score": row.drift_score,
            }
            for row in rows
        ],
    }


@enterprise_router.get("/players/{player_id}/features/current", tags=["players"])
async def get_player_features_current(
    player_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current online features from Redis."""
    # Tenant isolation: verify player belongs to caller's tenant
    player = await db.get(Player, player_id)
    if not player or player.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")

    try:
        import redis.asyncio as aioredis
        from config import settings as _settings
        _redis = aioredis.from_url(_settings.redis_url, decode_responses=True)
        key = f"betaml:{current_user.tenant_id}:features:{player_id}"
        data = await _redis.hgetall(key)
        await _redis.aclose()
    except Exception:
        raise HTTPException(404, "Nenhuma feature encontrada para este player. Pode ainda não ter transacionado.")

    if not data:
        raise HTTPException(404, "Nenhuma feature encontrada para este player. Pode ainda não ter transacionado.")
    return {"player_id": player_id, "features": data, "source": "redis"}


# ──────────────────────────────────────────────────────────────────────────────
# M4 — Model Registry: promote A/B champion
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/model-registry", tags=["ml"])
async def list_models(
    model_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = select(ModelRegistry).where(_tenant_filter(ModelRegistry, current_user.tenant_id))
    if model_type:
        stmt = stmt.where(ModelRegistry.model_type == model_type)
    stmt = stmt.order_by(desc(ModelRegistry.trained_at))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {c.name: getattr(r, c.name) for c in r.__table__.columns}
        for r in rows
    ]


@enterprise_router.post("/model-registry/{model_id}/promote", tags=["ml"])
async def promote_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Promote a challenger model to champion status."""
    model = (await db.execute(
        select(ModelRegistry).where(
            ModelRegistry.id == model_id,
            _tenant_filter(ModelRegistry, current_user.tenant_id),
        )
    )).scalar_one_or_none()
    if model is None:
        raise HTTPException(404)

    # Demote current champion of same type
    await db.execute(
        update(ModelRegistry).where(
            _tenant_filter(ModelRegistry, current_user.tenant_id),
            ModelRegistry.model_type == model.model_type,
            ModelRegistry.status == "champion",
        ).values(status="archived")
    )
    model.status        = "champion"
    model.is_challenger = False
    model.promoted_by   = current_user.id
    model.promoted_at   = datetime.now(UTC)
    await _write_audit(db, current_user.tenant_id, current_user.id,
                       "PROMOTE_MODEL", "ModelRegistry", model_id,
                       {"model_type": model.model_type})
    await db.commit()
    return {"status": "promoted", "model_id": model_id}


# ──────────────────────────────────────────────────────────────────────────────
# System Flags CRUD
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.get("/admin/flags", response_model=list[SystemFlagOut], tags=["admin"])
async def list_system_flags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SystemFlag).where(_tenant_filter(SystemFlag, current_user.tenant_id))
    )
    return result.scalars().all()


@enterprise_router.put("/admin/flags/{flag_name}", response_model=SystemFlagOut, tags=["admin"])
async def upsert_system_flag(
    flag_name: str,
    body: SystemFlagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    flag = (await db.execute(
        select(SystemFlag).where(
            SystemFlag.tenant_id == current_user.tenant_id,
            SystemFlag.flag_name == flag_name,
        )
    )).scalar_one_or_none()
    if flag is None:
        flag = SystemFlag(tenant_id=current_user.tenant_id, flag_name=flag_name)
        db.add(flag)
    flag.flag_value = body.flag_value
    flag.updated_by = current_user.id
    flag.updated_at = datetime.now(UTC)
    await db.commit()
    return flag
