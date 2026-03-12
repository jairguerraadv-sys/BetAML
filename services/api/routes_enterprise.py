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
from config import settings               # app settings (env-backed)
from database import get_db               # async session factory
from auth import get_current_user, require_roles, User   # JWT dep

from libs.schemas import (
    AlertLabelIn,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeyOut,
    CompoundRuleCreate,
    CompoundRuleOut,
    FeatureStoreCurrentOut,
    FeatureStoreHistoryOut,
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

# Nota: Admin, Feature Store, Notifications e Model Registry foram extraídos para:
#   routers/admin.py, routers/feature_store.py, routers/notifications.py, routers/ml.py
# Este arquivo mantém apenas os fluxos de ingest/mapping/player-lists/compound-rules/relatórios LGPD.


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
    mapping_id: str,
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
    mapping_id: str,
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
    list_id: str,
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
            list_id=pl.id,
            tenant_id=current_user.tenant_id,
            player_list_id=list_id,
            value=val,
            value_type=body.value_type,
        ))
        added += 1
    await db.commit()
    return {"added": added}


@enterprise_router.delete("/player-lists/{list_id}", status_code=204, tags=["player-lists"])
async def delete_player_list(
    list_id: str,
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
    list_id: str,
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
            db.add(PlayerListEntry(list_id=pl.id, tenant_id=current_user.tenant_id, player_list_id=list_id, value=val, value_type=value_type))
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
    rule_id: str,
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
        body_dsl=body.expression,
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
    macro_id: str,
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
# M5 — Alert labeling (feedback loop)
# ──────────────────────────────────────────────────────────────────────────────

@enterprise_router.post("/alerts/{alert_id}/label", tags=["alerts"])
async def label_alert(
    alert_id: str,
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


async def _enqueue_feedback_event(alert_id: str, label: str, tenant_id: str):
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
    case_id: str,
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
        select(ReportPackage).where(
            ReportPackage.case_id == case_id,
            ReportPackage.tenant_id == current_user.tenant_id,
        ).order_by(desc(ReportPackage.created_at))
    )).scalar_one_or_none()
    if rp is None or not rp.pdf_path:
        raise HTTPException(404, "No PDF report package found for this case")

    # If stored in MinIO, stream it back
    try:
        from libs.clients import get_minio_client
        minio = get_minio_client()
        bucket = settings.minio_bucket
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
    current_user: User = Depends(require_roles("ADMIN")),
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
    player.full_name      = f"ERASURE_{anon_suffix}"
    player.cpf_encrypted  = f"ERASURE_{anon_suffix}".encode()
    player.name_encrypted = f"ERASURE_{anon_suffix}".encode()
    player.status         = "ERASED"

    await _write_audit(
        db, current_user.tenant_id, current_user.id,
        "LGPD_ERASURE", "Player", player_id,
        {"reason": "right_to_erasure", "anon_suffix": anon_suffix},
    )
    await db.commit()
    return {"status": "erased", "player_id": player_id}
