"""
BetAML API — FastAPI application
Routes:
  /auth          - login, refresh, logout, /me
  /ingest        - file, event, batch, jobs
  /rules         - CRUD + simulate
  /alerts        - list, detail, triage, close, link-to-case
  /cases         - CRUD, assign, events, evidence, report-package
  /audit-logs    - listagem (ADMIN/AUDITOR)
  /players       - listagem + perfil
  /mappings      - CRUD MappingConfig
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

# Adiciona libs ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/app/libs")

from auth import (
    create_access_token,
    decrypt_pii,
    encrypt_pii,
    get_current_user,
    hash_password,
    mask_cpf,
    require_roles,
    verify_password,
)
from config import settings
from database import AsyncSessionLocal, engine, get_db
from models import (
    Alert,
    AuditLog,
    Base,
    Case,
    CaseEvent,
    IngestJob,
    MappingConfig,
    Player,
    ReportPackage,
    RuleDefinition,
    Tenant,
    User,
)

logger = structlog.get_logger()

app = FastAPI(
    title="BetAML API",
    description="PLD/FT Platform para Operadores de Apostas",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Enterprise routes ────────────────────────────
try:
    from routes_enterprise import enterprise_router
    app.include_router(enterprise_router)
except ImportError:
    pass  # graceful degradation if file not yet in image

# ─── Prometheus metrics ───────────────────────────
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_group_untemplated=True,
    excluded_handlers=["/metrics", "/health", "/docs", "/redoc", "/openapi.json"],
).instrument(app).expose(app, include_in_schema=False, tags=["observability"])

# ─── Producer global ──────────────────────────────
_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        try:
            from libs.clients import KafkaProducerClient
            _producer = KafkaProducerClient(settings.kafka_bootstrap_servers)
            await _producer.start()
        except Exception as e:
            logger.warning("kafka_producer_unavailable", error=str(e))
    return _producer


# ─── Startup / shutdown ───────────────────────────
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await get_producer()
    logger.info("betaml_api_started", env=settings.environment)


@app.on_event("shutdown")
async def shutdown():
    if _producer:
        await _producer.stop()


# ─── Helper: audit log ────────────────────────────
async def write_audit(
    db: AsyncSession,
    tenant_id: str,
    user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
):
    al = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip_address=ip,
    )
    db.add(al)
    await db.flush()


# ─── Health ───────────────────────────────────────
@app.get("/health", tags=["infra"])
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ═══════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == body.username, User.active == True))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Usuário ou senha inválidos")
    token = create_access_token({"sub": user.id, "tenant_id": user.tenant_id, "role": user.role})
    return TokenResponse(access_token=token, role=user.role, tenant_id=user.tenant_id)


@app.get("/me", tags=["auth"])
async def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "tenant_id": current_user.tenant_id,
    }


@app.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
async def refresh(current_user: User = Depends(get_current_user)):
    """Re-emite um novo token JWT a partir de um token ainda válido."""
    token = create_access_token({
        "sub": current_user.id,
        "tenant_id": current_user.tenant_id,
        "role": current_user.role,
    })
    return TokenResponse(access_token=token, role=current_user.role, tenant_id=current_user.tenant_id)


@app.post("/auth/logout", tags=["auth"])
async def logout():
    # JWT stateless — cliente descarta o token
    return {"message": "Logout realizado"}


# ═══════════════════════════════════════════════════
# INGEST
# ═══════════════════════════════════════════════════

class IngestEventRequest(BaseModel):
    source_system: str
    entity_type: str
    source_event_id: Optional[str] = None
    payload: dict[str, Any]
    mapping_config_id: Optional[str] = None


@app.post("/ingest/event", status_code=202, tags=["ingest"])
async def ingest_event(
    body: IngestEventRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    event_id = str(uuid.uuid4())
    source_event_id = body.source_event_id or event_id
    envelope = {
        "event_id": event_id,
        "tenant_id": current_user.tenant_id,
        "source_system": body.source_system,
        "source_event_id": source_event_id,
        "schema_version": 1,
        "entity_type": body.entity_type,
        "occurred_at": datetime.utcnow().isoformat(),
        "payload": body.payload,
        "raw_payload": body.payload,
        "ingest_metadata": {
            "received_at": datetime.utcnow().isoformat(),
            "mapper_version": "1.0",
        },
    }
    producer = await get_producer()
    if producer:
        topic = f"raw.{body.entity_type.lower()}s"
        await producer.send(topic, envelope, key=source_event_id)
    return {"event_id": event_id, "status": "queued"}


@app.post("/ingest/batch", status_code=202, tags=["ingest"])
async def ingest_batch(
    events: list[IngestEventRequest],
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    results = []
    producer = await get_producer()
    for body in events:
        event_id = str(uuid.uuid4())
        source_event_id = body.source_event_id or event_id
        envelope = {
            "event_id": event_id,
            "tenant_id": current_user.tenant_id,
            "source_system": body.source_system,
            "source_event_id": source_event_id,
            "schema_version": 1,
            "entity_type": body.entity_type,
            "occurred_at": datetime.utcnow().isoformat(),
            "payload": body.payload,
            "raw_payload": body.payload,
            "ingest_metadata": {"received_at": datetime.utcnow().isoformat(), "mapper_version": "1.0"},
        }
        if producer:
            topic = f"raw.{body.entity_type.lower()}s"
            await producer.send(topic, envelope, key=source_event_id)
        results.append({"event_id": event_id, "status": "queued"})
    return {"count": len(results), "results": results}


@app.post("/ingest/file", status_code=202, tags=["ingest"])
async def ingest_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_system: str = Form(...),
    mapping_config_id: Optional[str] = Form(None),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    job = IngestJob(
        tenant_id=current_user.tenant_id,
        source_system=source_system,
        mapping_config_id=mapping_config_id,
        file_name=file.filename,
        file_size_bytes=len(content),
        status="QUEUED",
        created_by=current_user.id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Publicar no Kafka para processamento async
    producer = await get_producer()
    if producer:
        msg = {
            "job_id": job.id,
            "tenant_id": current_user.tenant_id,
            "source_system": source_system,
            "mapping_config_id": mapping_config_id,
            "file_name": file.filename,
            "file_content_b64": __import__("base64").b64encode(content).decode(),
        }
        await producer.send("ingest.jobs", msg, key=job.id)

    return {"job_id": job.id, "status": "QUEUED", "file_name": file.filename}


@app.get("/ingest/jobs", tags=["ingest"])
async def list_ingest_jobs(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    q = select(IngestJob).where(IngestJob.tenant_id == current_user.tenant_id)
    if status_filter:
        q = q.where(IngestJob.status == status_filter)
    q = q.order_by(IngestJob.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    jobs = result.scalars().all()
    return [
        {
            "id": j.id, "source_system": j.source_system, "file_name": j.file_name,
            "status": j.status, "total_records": j.total_records,
            "processed_records": j.processed_records, "created_at": j.created_at,
        }
        for j in jobs
    ]


# ═══════════════════════════════════════════════════
# RULES
# ═══════════════════════════════════════════════════

class RuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    status: str = "ACTIVE"
    severity: str = "MEDIUM"
    scope: str = "TRANSACTION"
    condition_dsl: str
    params: dict[str, Any] = {}


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    condition_dsl: Optional[str] = None
    params: Optional[dict[str, Any]] = None


@app.get("/rules", tags=["rules"])
async def list_rules(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(RuleDefinition).where(RuleDefinition.tenant_id == current_user.tenant_id)
    result = await db.execute(q)
    rules = result.scalars().all()
    return [
        {
            "id": r.id, "name": r.name, "status": r.status, "severity": r.severity,
            "scope": r.scope, "condition_dsl": r.condition_dsl, "params": r.params,
            "version": r.version, "created_at": r.created_at,
        }
        for r in rules
    ]


@app.post("/rules", status_code=201, tags=["rules"])
async def create_rule(
    body: RuleCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    from libs.dsl_parser import validate_dsl
    ok, msg = validate_dsl(body.condition_dsl)
    if not ok:
        raise HTTPException(400, detail=f"DSL inválido: {msg}")
    rule = RuleDefinition(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        status=body.status,
        severity=body.severity,
        scope=body.scope,
        condition_dsl=body.condition_dsl,
        params=body.params,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await write_audit(db, current_user.tenant_id, current_user.id, "CREATE", "RuleDefinition", rule.id, after=body.model_dump())
    await db.commit()
    await db.refresh(rule)
    return {"id": rule.id, "name": rule.name, "status": rule.status}


@app.get("/rules/{rule_id}", tags=["rules"])
async def get_rule(
    rule_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(RuleDefinition, rule_id)
    if not r or r.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Regra não encontrada")
    return {
        "id": r.id, "name": r.name, "status": r.status, "severity": r.severity,
        "scope": r.scope, "condition_dsl": r.condition_dsl, "params": r.params,
        "version": r.version, "description": r.description, "created_at": r.created_at,
    }


@app.put("/rules/{rule_id}", tags=["rules"])
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(RuleDefinition, rule_id)
    if not r or r.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Regra não encontrada")
    before = {"status": r.status, "condition_dsl": r.condition_dsl, "version": r.version}
    if body.condition_dsl:
        from libs.dsl_parser import validate_dsl
        ok, msg = validate_dsl(body.condition_dsl)
        if not ok:
            raise HTTPException(400, detail=f"DSL inválido: {msg}")
        r.condition_dsl = body.condition_dsl
        r.version += 1
    if body.name:        r.name        = body.name
    if body.description: r.description = body.description
    if body.status:      r.status      = body.status
    if body.severity:    r.severity    = body.severity
    if body.params is not None: r.params = body.params
    r.updated_by = current_user.id
    await write_audit(db, current_user.tenant_id, current_user.id, "UPDATE", "RuleDefinition", rule_id, before=before, after=body.model_dump())
    await db.commit()
    return {"id": r.id, "version": r.version, "status": r.status}


@app.delete("/rules/{rule_id}", tags=["rules"])
async def delete_rule(
    rule_id: str,
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.get(RuleDefinition, rule_id)
    if not r or r.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Regra não encontrada")
    r.status = "INACTIVE"
    await write_audit(db, current_user.tenant_id, current_user.id, "DELETE", "RuleDefinition", rule_id)
    await db.commit()
    return {"message": "Regra desativada"}


class SimulateRequest(BaseModel):
    events: list[dict[str, Any]]


@app.post("/rules/{rule_id}/simulate", tags=["rules"])
async def simulate_rule(
    rule_id: str,
    body: SimulateRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    from libs.dsl_parser import eval_dsl
    r = await db.get(RuleDefinition, rule_id)
    if not r or r.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Regra não encontrada")
    results = []
    for evt in body.events:
        try:
            ctx = {
                "transaction": evt.get("transaction", evt),
                "bet":         evt.get("bet", {}),
                "player":      evt.get("player", {}),
                "features":    evt.get("features", {}),
                "params":      r.params,
            }
            matched = eval_dsl(r.condition_dsl, ctx)
        except Exception as e:
            matched = False
            results.append({"matched": False, "error": str(e), "event": evt})
            continue
        results.append({"matched": matched, "event": evt})
    return {"rule_id": rule_id, "results": results, "matches": sum(1 for r in results if r.get("matched"))}


# ═══════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════

@app.get("/alerts", tags=["alerts"])
async def list_alerts(
    severity: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    player_id: Optional[str] = None,
    rule_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Alert).where(Alert.tenant_id == current_user.tenant_id)
    if severity:      q = q.where(Alert.severity == severity)
    if status_filter: q = q.where(Alert.status == status_filter)
    if player_id:     q = q.where(Alert.player_id == player_id)
    if rule_id:       q = q.where(Alert.rule_id == rule_id)
    q = q.order_by(Alert.created_at.desc()).limit(limit).offset(offset)

    # count
    count_q = select(sqlfunc.count()).select_from(Alert).where(Alert.tenant_id == current_user.tenant_id)
    total = (await db.execute(count_q)).scalar()

    result = await db.execute(q)
    alerts = result.scalars().all()
    return {
        "total": total,
        "items": [
            {
                "id": a.id, "severity": a.severity, "status": a.status,
                "title": a.title, "alert_type": a.alert_type,
                "player_id": a.player_id, "rule_id": a.rule_id,
                "anomaly_score": float(a.anomaly_score) if a.anomaly_score else None,
                "case_id": a.case_id, "created_at": a.created_at,
            }
            for a in alerts
        ],
    }


@app.get("/alerts/{alert_id}", tags=["alerts"])
async def get_alert(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    return {
        "id": a.id, "severity": a.severity, "status": a.status,
        "title": a.title, "description": a.description,
        "alert_type": a.alert_type, "evidence": a.evidence,
        "player_id": a.player_id, "rule_id": a.rule_id,
        "anomaly_score": float(a.anomaly_score) if a.anomaly_score else None,
        "source_event_id": a.source_event_id,
        "case_id": a.case_id, "created_at": a.created_at,
    }


class TriageRequest(BaseModel):
    notes: Optional[str] = None


@app.post("/alerts/{alert_id}/triage", tags=["alerts"])
async def triage_alert(
    alert_id: str,
    body: TriageRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    a.status = "IN_REVIEW"
    a.triaged_by = current_user.id
    a.triaged_at = datetime.utcnow()
    await write_audit(db, current_user.tenant_id, current_user.id, "TRIAGE", "Alert", alert_id)
    await db.commit()
    return {"id": alert_id, "status": "IN_REVIEW"}


@app.post("/alerts/{alert_id}/close", tags=["alerts"])
async def close_alert(
    alert_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    a.status = "CLOSED"
    await write_audit(db, current_user.tenant_id, current_user.id, "CLOSE", "Alert", alert_id)
    await db.commit()
    return {"id": alert_id, "status": "CLOSED"}


class LinkCaseRequest(BaseModel):
    case_id: str


@app.post("/alerts/{alert_id}/link-to-case", tags=["alerts"])
async def link_alert_to_case(
    alert_id: str,
    body: LinkCaseRequest,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    a = await db.get(Alert, alert_id)
    c = await db.get(Case, body.case_id)
    if not a or a.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Alerta não encontrado")
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    a.case_id = body.case_id
    await write_audit(db, current_user.tenant_id, current_user.id, "LINK_CASE", "Alert", alert_id, after={"case_id": body.case_id})
    await db.commit()
    return {"alert_id": alert_id, "case_id": body.case_id}


# ═══════════════════════════════════════════════════
# CASES
# ═══════════════════════════════════════════════════

class CaseCreate(BaseModel):
    player_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str = "HIGH"


@app.post("/cases", status_code=201, tags=["cases"])
async def create_case(
    body: CaseCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = Case(
        tenant_id=current_user.tenant_id,
        player_id=body.player_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        created_by=current_user.id,
    )
    db.add(c)
    await db.flush()
    await write_audit(db, current_user.tenant_id, current_user.id, "CREATE", "Case", c.id, after=body.model_dump())
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "title": c.title, "status": c.status}


@app.get("/cases", tags=["cases"])
async def list_cases(
    status_filter: Optional[str] = Query(None, alias="status"),
    player_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Case).where(Case.tenant_id == current_user.tenant_id)
    if status_filter: q = q.where(Case.status == status_filter)
    if player_id:     q = q.where(Case.player_id == player_id)
    q = q.order_by(Case.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    cases = result.scalars().all()
    return [
        {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "player_id": c.player_id, "assigned_to": c.assigned_to, "created_at": c.created_at,
        }
        for c in cases
    ]


@app.get("/cases/{case_id}", tags=["cases"])
async def get_case(
    case_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    # buscar alerts vinculados
    alerts_q = select(Alert).where(Alert.case_id == case_id)
    alerts = (await db.execute(alerts_q)).scalars().all()
    # buscar eventos do caso
    events_q = select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at)
    events = (await db.execute(events_q)).scalars().all()
    return {
        "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
        "description": c.description, "player_id": c.player_id,
        "assigned_to": c.assigned_to, "created_at": c.created_at,
        "alerts": [{"id": a.id, "severity": a.severity, "title": a.title} for a in alerts],
        "timeline": [{"id": e.id, "event_type": e.event_type, "content": e.content, "created_at": e.created_at} for e in events],
    }


class AssignRequest(BaseModel):
    user_id: str


@app.post("/cases/{case_id}/assign", tags=["cases"])
async def assign_case(
    case_id: str,
    body: AssignRequest,
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    c.assigned_to = body.user_id
    evt = CaseEvent(case_id=case_id, tenant_id=current_user.tenant_id,
                    event_type="ASSIGNMENT", content={"assigned_to": body.user_id}, created_by=current_user.id)
    db.add(evt)
    await db.commit()
    return {"case_id": case_id, "assigned_to": body.user_id}


class CaseEventCreate(BaseModel):
    event_type: str = "NOTE"
    content: dict[str, Any]


@app.post("/cases/{case_id}/events", status_code=201, tags=["cases"])
async def add_case_event(
    case_id: str,
    body: CaseEventCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    if body.event_type == "STATUS_CHANGE":
        new_status = body.content.get("new_status")
        if new_status:
            c.status = new_status
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type=body.event_type, content=body.content, created_by=current_user.id,
    )
    db.add(evt)
    await write_audit(db, current_user.tenant_id, current_user.id, f"CASE_{body.event_type}", "Case", case_id, after=body.content)
    await db.commit()
    await db.refresh(evt)
    return {"id": evt.id, "event_type": evt.event_type, "created_at": evt.created_at}


@app.post("/cases/{case_id}/evidence", tags=["cases"])
async def upload_evidence(
    case_id: str,
    file: UploadFile = File(...),
    description: str = Form(""),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    # Em prod: salvar no MinIO. Para MVP: registra no CaseEvent.
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="EVIDENCE_UPLOAD",
        content={"file_name": file.filename, "description": description, "size": 0},
        created_by=current_user.id,
    )
    db.add(evt)
    await db.commit()
    return {"case_id": case_id, "file_name": file.filename, "status": "uploaded"}


@app.post("/cases/{case_id}/report-package", status_code=201, tags=["cases"])
async def generate_report_package(
    case_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")

    # Buscar alerts + events
    alerts_q  = select(Alert).where(Alert.case_id == case_id)
    alerts    = (await db.execute(alerts_q)).scalars().all()
    events_q  = select(CaseEvent).where(CaseEvent.case_id == case_id)
    events    = (await db.execute(events_q)).scalars().all()

    # Player info
    player_info: dict = {}
    if c.player_id:
        p = await db.get(Player, c.player_id)
        if p:
            cpf_plain = decrypt_pii(p.cpf_encrypted)
            player_info = {
                "player_id": p.id,
                "external_player_id": p.external_player_id,
                "cpf_masked": mask_cpf(cpf_plain),
                "pep_flag": p.pep_flag,
                "risk_score": float(p.risk_score),
            }

    payload = {
        "report_id": str(uuid.uuid4()),
        "generated_at": datetime.utcnow().isoformat(),
        "generated_by": current_user.username,
        "case": {"id": c.id, "title": c.title, "status": c.status, "severity": c.severity},
        "player": player_info,
        "alerts": [
            {
                "id": a.id, "title": a.title, "severity": a.severity,
                "alert_type": a.alert_type, "evidence": a.evidence,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in alerts
        ],
        "timeline": [
            {"event_type": e.event_type, "content": e.content, "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in events
        ],
    }

    rp = ReportPackage(
        tenant_id=current_user.tenant_id,
        case_id=case_id,
        player_id=c.player_id,
        payload=payload,
        created_by=current_user.id,
    )
    db.add(rp)
    evt = CaseEvent(case_id=case_id, tenant_id=current_user.tenant_id,
                    event_type="REPORT_GENERATED", content={"report_id": payload["report_id"]},
                    created_by=current_user.id)
    db.add(evt)
    await write_audit(db, current_user.tenant_id, current_user.id, "GENERATE_REPORT", "Case", case_id)
    await db.commit()
    await db.refresh(rp)
    return {"report_package_id": rp.id, "payload": payload}


# ═══════════════════════════════════════════════════
# PLAYERS
# ═══════════════════════════════════════════════════

@app.get("/players", tags=["players"])
async def list_players(
    limit: int = Query(50, le=200),
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Player).where(Player.tenant_id == current_user.tenant_id).limit(limit).offset(offset)
    players = (await db.execute(q)).scalars().all()
    return [
        {
            "id": p.id, "external_player_id": p.external_player_id,
            "cpf_masked": mask_cpf(decrypt_pii(p.cpf_encrypted)),
            "pep_flag": p.pep_flag, "risk_score": float(p.risk_score),
            "created_at": p.created_at,
        }
        for p in players
    ]


@app.get("/players/{player_id}", tags=["players"])
async def get_player(
    player_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    p = await db.get(Player, player_id)
    if not p or p.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Player não encontrado")
    cpf_plain = decrypt_pii(p.cpf_encrypted)
    show_full = current_user.role in ("ADMIN", "AML_ANALYST")
    return {
        "id": p.id, "external_player_id": p.external_player_id,
        "cpf": cpf_plain if show_full else mask_cpf(cpf_plain),
        "pep_flag": p.pep_flag, "risk_score": float(p.risk_score),
        "declared_income_monthly": float(p.declared_income_monthly) if p.declared_income_monthly else None,
        "last_scored_at": p.last_scored_at,
    }


# ═══════════════════════════════════════════════════
# MAPPING CONFIGS
# ═══════════════════════════════════════════════════

class MappingCreate(BaseModel):
    name: str
    source_system: str
    entity_type: str
    config_json: dict[str, Any]
    version: str = "1.0"


@app.get("/mappings", tags=["mappings"])
async def list_mappings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(MappingConfig).where(MappingConfig.tenant_id == current_user.tenant_id, MappingConfig.active == True)
    mc = (await db.execute(q)).scalars().all()
    return [{"id": m.id, "name": m.name, "source_system": m.source_system, "entity_type": m.entity_type, "version": m.version} for m in mc]


@app.post("/mappings", status_code=201, tags=["mappings"])
async def create_mapping(
    body: MappingCreate,
    current_user: User = Depends(require_roles("ADMIN")),
    db: AsyncSession = Depends(get_db),
):
    mc = MappingConfig(
        tenant_id=current_user.tenant_id,
        name=body.name,
        source_system=body.source_system,
        entity_type=body.entity_type,
        config_json=body.config_json,
        version=body.version,
        created_by=current_user.id,
    )
    db.add(mc)
    await db.commit()
    await db.refresh(mc)
    return {"id": mc.id, "name": mc.name}


@app.post("/mappings/{mapping_id}/test", tags=["mappings"])
async def test_mapping(
    mapping_id: str,
    sample: dict[str, Any],
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    mc = await db.get(MappingConfig, mapping_id)
    if not mc or mc.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Mapping não encontrado")
    try:
        from libs.mapping import MappingEngine
        engine = MappingEngine(mc.config_json)
        result = engine.apply(sample)
        return {"status": "ok", "canonical": result}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ═══════════════════════════════════════════════════
# AUDIT LOGS
# ═══════════════════════════════════════════════════

@app.get("/audit-logs", tags=["audit"])
async def list_audit_logs(
    entity_type: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    current_user: User = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
):
    q = select(AuditLog).where(AuditLog.tenant_id == current_user.tenant_id)
    if entity_type: q = q.where(AuditLog.entity_type == entity_type)
    q = q.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    logs = (await db.execute(q)).scalars().all()
    return [
        {
            "id": l.id, "action": l.action, "entity_type": l.entity_type,
            "entity_id": l.entity_id, "user_id": l.user_id,
            "before": l.before, "after": l.after, "created_at": l.created_at,
        }
        for l in logs
    ]
