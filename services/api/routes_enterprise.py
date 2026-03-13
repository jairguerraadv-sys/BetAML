"""
Enterprise routes — M1-M8 endpoints.

Mounted on `app` via `app.include_router(enterprise_router)` in main.py.
All endpoints require a valid JWT (get_current_user dependency) plus
tenant isolation enforced by current_user.tenant_id.
"""
from __future__ import annotations

import asyncio
import csv
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
from database import AsyncSessionLocal, get_db               # async session factory
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
    Bet,
    Case,
    CompoundRule,
    FeatureSnapshot,
    FinancialTransaction,
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


async def _enqueue_feedback_event(alert_id: str, label: str, tenant_id: str) -> None:
    """Publish labeled event to Kafka for ML retraining pipeline.

    Retries up to 2 times on transient failures.  On final failure:
    - Logs a WARNING via structlog (includes alert_id, tenant_id, error)
    - Stores a Notification record for every ADMIN user of the tenant so the
      failure is visible in the UI and can trigger manual remediation.
    """
    MAX_RETRIES = 2
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            from aiokafka import AIOKafkaProducer
            producer = AIOKafkaProducer(
                bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP", "redpanda:9092")
            )
            await producer.start()
            try:
                await producer.send_and_wait(
                    "feedback.labels",
                    json.dumps({
                        "alert_id": alert_id,
                        "label": label,
                        "tenant_id": tenant_id,
                        "ts": datetime.now(UTC).isoformat(),
                    }).encode(),
                )
                return  # success — exit immediately
            finally:
                await producer.stop()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))

    # All retries exhausted — log warning and store in-app notification
    logger.warning(
        "feedback_event_publish_failed",
        alert_id=alert_id,
        tenant_id=tenant_id,
        label=label,
        error=str(last_exc),
        attempts=MAX_RETRIES + 1,
    )

    try:
        async with AsyncSessionLocal() as _db:
            admin_ids = list(
                (
                    await _db.execute(
                        select(User.id).where(
                            User.tenant_id == tenant_id,
                            User.role == "ADMIN",
                            User.active == True,  # noqa: E712
                        )
                    )
                ).scalars().all()
            )
            for admin_id in admin_ids:
                _db.add(
                    Notification(
                        tenant_id=tenant_id,
                        user_id=admin_id,
                        type="SYSTEM_ERROR",
                        title="Falha na publicação de feedback label",
                        body=(
                            f"Feedback label para alert {alert_id} falhou ao publicar "
                            f"após {MAX_RETRIES + 1} tentativas — revisão manual necessária."
                        ),
                        reference_type="alert",
                        reference_id=alert_id,
                    )
                )
            await _db.commit()
    except Exception as db_exc:  # noqa: BLE001
        logger.error(
            "feedback_notification_store_failed",
            alert_id=alert_id,
            error=str(db_exc),
        )


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


async def _build_monthly_report(
    tenant_id: str,
    date_from: datetime,
    date_to: datetime,
    db: AsyncSession,
) -> dict:
    """Aggregate compliance statistics for a given period and tenant.

    All queries enforce tenant isolation via tenant_id filter.

    Returns a dict with:
      - period: {"from", "to"}
      - alerts_by_severity: {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
      - cases_summary: {status_key: count, ...}  (includes open/investigating/closed/reported)
      - top_rules_by_fires: [{rule_id, rule_name, fires}]  — top 10
      - top_players_by_risk: [{player_id, external_id, avg_risk_score}]  — top 10
      - total_ingested_events: int (FinancialTransaction + Bet in period)
      - false_positive_rate: float | None
      - generated_at: ISO string
    """
    # ── 1. Alerts by severity ──────────────────────────────────────────────────
    sev_rows = (await db.execute(
        select(Alert.severity, func.count().label("cnt"))
        .where(
            Alert.tenant_id == tenant_id,
            Alert.created_at >= date_from,
            Alert.created_at <= date_to,
        )
        .group_by(Alert.severity)
    )).all()
    alerts_by_severity: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for row in sev_rows:
        if row.severity in alerts_by_severity:
            alerts_by_severity[row.severity] = row.cnt

    # ── 2. Cases summary ───────────────────────────────────────────────────────
    case_rows = (await db.execute(
        select(Case.status, func.count().label("cnt"))
        .where(
            Case.tenant_id == tenant_id,
            Case.created_at >= date_from,
            Case.created_at <= date_to,
        )
        .group_by(Case.status)
    )).all()
    cases_summary: dict[str, int] = {}
    for row in case_rows:
        key = row.status.lower() if row.status else "unknown"
        cases_summary[key] = row.cnt
    # Ensure expected keys are always present
    for expected in ("open", "investigating", "closed", "reported"):
        cases_summary.setdefault(expected, 0)

    # ── 3. Top 10 rules by alert fires ────────────────────────────────────────
    rule_rows = (await db.execute(
        select(
            Alert.rule_id,
            RuleDefinition.name.label("rule_name"),
            func.count().label("fires"),
        )
        .join(RuleDefinition, RuleDefinition.id == Alert.rule_id, isouter=True)
        .where(
            Alert.tenant_id == tenant_id,
            Alert.created_at >= date_from,
            Alert.created_at <= date_to,
            Alert.rule_id.isnot(None),
        )
        .group_by(Alert.rule_id, RuleDefinition.name)
        .order_by(desc("fires"))
        .limit(10)
    )).all()
    top_rules_by_fires = [
        {
            "rule_id": str(r.rule_id),
            "rule_name": r.rule_name or "(desconhecido)",
            "fires": r.fires,
        }
        for r in rule_rows
    ]

    # ── 4. Top 10 players by current risk score ───────────────────────────────
    player_rows = (await db.execute(
        select(Player.id, Player.external_id, Player.risk_score)
        .where(Player.tenant_id == tenant_id)
        .order_by(Player.risk_score.desc())
        .limit(10)
    )).all()
    top_players_by_risk = [
        {
            "player_id": str(r.id),
            "external_id": r.external_id or "",
            "avg_risk_score": float(r.risk_score or 0),
        }
        for r in player_rows
    ]

    # ── 5. Total ingested events (FinancialTransaction + Bet) ─────────────────
    tx_count = (await db.execute(
        select(func.count()).where(
            and_(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.created_at >= date_from,
                FinancialTransaction.created_at <= date_to,
            )
        )
    )).scalar_one()
    bet_count = (await db.execute(
        select(func.count()).where(
            and_(
                Bet.tenant_id == tenant_id,
                Bet.created_at >= date_from,
                Bet.created_at <= date_to,
            )
        )
    )).scalar_one()
    total_ingested_events = (tx_count or 0) + (bet_count or 0)

    # ── 6. False positive rate ─────────────────────────────────────────────────
    label_rows = (await db.execute(
        select(Alert.label, func.count().label("cnt"))
        .where(
            Alert.tenant_id == tenant_id,
            Alert.labeled_at >= date_from,
            Alert.labeled_at <= date_to,
            Alert.label.isnot(None),
        )
        .group_by(Alert.label)
    )).all()
    total_labeled = sum(r.cnt for r in label_rows)
    fp_count = sum(r.cnt for r in label_rows if r.label == "FALSE_POSITIVE")
    false_positive_rate: float | None = (
        round(fp_count / total_labeled, 4) if total_labeled > 0 else None
    )

    return {
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
        },
        "alerts_by_severity": alerts_by_severity,
        "cases_summary": cases_summary,
        "top_rules_by_fires": top_rules_by_fires,
        "top_players_by_risk": top_players_by_risk,
        "total_ingested_events": total_ingested_events,
        "false_positive_rate": false_positive_rate,
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def _build_monthly_report_background(
    tenant_id: str,
    year: int,
    month: int,
) -> None:
    """Background wrapper: creates its own DB session and runs _build_monthly_report.

    Computes date_from / date_to from year + month (full calendar month, UTC).
    """
    import calendar
    date_from = datetime(year, month, 1, tzinfo=UTC)
    last_day = calendar.monthrange(year, month)[1]
    date_to = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    try:
        async with AsyncSessionLocal() as _db:
            report = await _build_monthly_report(tenant_id, date_from, date_to, _db)
            logger.info(
                "monthly_report_background_completed",
                tenant_id=tenant_id,
                year=year,
                month=month,
                total_alerts=sum(report["alerts_by_severity"].values()),
                total_events=report["total_ingested_events"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "monthly_report_background_failed",
            tenant_id=tenant_id,
            year=year,
            month=month,
            error=str(exc),
        )


@enterprise_router.post("/reports/monthly-summary", status_code=202, tags=["reports"])
async def generate_monthly_report(
    body: MonthlyReportIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger async generation of monthly compliance summary report (202 Accepted).

    The report is built in a background task.  Use GET /reports/monthly-summary
    with date_from / date_to query params to fetch the result synchronously.
    """
    background_tasks.add_task(
        _build_monthly_report_background,
        current_user.tenant_id,
        body.year,
        body.month,
    )
    return {"status": "queued", "year": body.year, "month": body.month}


@enterprise_router.get("/reports/monthly-summary", tags=["reports"])
async def get_monthly_summary(
    date_from: str = Query(..., description="Data inicial YYYY-MM-DD (inclusivo)"),
    date_to: str = Query(..., description="Data final YYYY-MM-DD (inclusivo)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retorna o sumário mensal de compliance de forma síncrona.

    Parâmetros de query:
        date_from: YYYY-MM-DD — início do período (00:00:00 UTC)
        date_to:   YYYY-MM-DD — fim do período (23:59:59 UTC)

    Returns:
        JSON com alerts_by_severity, cases_summary, top_rules_by_fires,
        top_players_by_risk, total_ingested_events, false_positive_rate e período.
    """
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=UTC
        )
    except ValueError:
        raise HTTPException(400, "date_from e date_to devem estar no formato YYYY-MM-DD")
    if df > dt:
        raise HTTPException(400, "date_from não pode ser posterior a date_to")
    return await _build_monthly_report(current_user.tenant_id, df, dt, db)


@enterprise_router.get("/reports/monthly-summary/csv", tags=["reports"])
async def get_monthly_summary_csv(
    date_from: str = Query(..., description="Data inicial YYYY-MM-DD"),
    date_to: str = Query(..., description="Data final YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Exporta o sumário mensal como arquivo CSV para download.

    Parâmetros de query:
        date_from: YYYY-MM-DD
        date_to:   YYYY-MM-DD

    Returns:
        text/csv — UTF-8 com BOM para compatibilidade com Microsoft Excel.
    """
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=UTC
        )
    except ValueError:
        raise HTTPException(400, "date_from e date_to devem estar no formato YYYY-MM-DD")
    if df > dt:
        raise HTTPException(400, "date_from não pode ser posterior a date_to")

    report = await _build_monthly_report(current_user.tenant_id, df, dt, db)

    output = io.StringIO()
    writer = csv.writer(output)

    # Metadata rows
    writer.writerow(["secao", "chave", "valor"])
    writer.writerow(["Periodo", "de", report["period"]["from"]])
    writer.writerow(["Periodo", "ate", report["period"]["to"]])
    writer.writerow(["Periodo", "gerado_em", report["generated_at"]])

    # Alerts by severity
    for sev, cnt in report["alerts_by_severity"].items():
        writer.writerow(["AlertasPorSeveridade", sev, cnt])

    # Cases summary
    for status_key, cnt in report["cases_summary"].items():
        writer.writerow(["ResumoDeOcorrencias", status_key, cnt])

    # Totals
    writer.writerow(["Totais", "eventos_ingeridos", report["total_ingested_events"]])
    writer.writerow([
        "Totais",
        "taxa_falso_positivo",
        report["false_positive_rate"]
        if report["false_positive_rate"] is not None
        else "N/D",
    ])

    # Top rules
    writer.writerow([])
    writer.writerow(["TopRegras", "rule_id", "rule_name", "disparos"])
    for r in report["top_rules_by_fires"]:
        writer.writerow(["TopRegras", r["rule_id"], r["rule_name"], r["fires"]])

    # Top players
    writer.writerow([])
    writer.writerow(["TopJogadores", "player_id", "external_id", "avg_risk_score"])
    for p in report["top_players_by_risk"]:
        writer.writerow(["TopJogadores", p["player_id"], p["external_id"], p["avg_risk_score"]])

    csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility
    filename = f"monthly_summary_{date_from}_{date_to}.csv"
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# NOTE: M6 — LGPD right to erasure was removed from this file.
# The canonical implementation lives in routers/players.py (POST /players/{id}/erase).
# A backward-compatible alias at /players/{id}/right-to-erasure is also in that router.
