"""routers/rules.py — CRUD de RuleDefinition + simulação DSL/histórica."""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from models import Alert, RuleDefinition, RuleMacro, User
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["rules"])


class RuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    status: str = "ACTIVE"
    severity: str = "MEDIUM"
    scope: str = "TRANSACTION"
    condition_dsl: str
    params: dict[str, Any] = {}
    weight: float = 0.5


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    condition_dsl: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    weight: Optional[float] = None


class SimulateRequest(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    from_date: Optional[date] = Field(default=None, alias="from")
    to_date: Optional[date] = Field(default=None, alias="to")
    player_ids: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ValidateDSLRequest(BaseModel):
    expression: str


async def _tenant_macros(db: AsyncSession, tenant_id: str) -> dict[str, str]:
    rows = (
        await db.execute(
            select(RuleMacro).where(RuleMacro.tenant_id == tenant_id)
        )
    ).scalars().all()
    return {row.name: row.expression for row in rows}


@router.post("/rules/validate")
async def validate_rule_dsl(
    body: ValidateDSLRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Valida sintaxe de uma expressão DSL sem persistir."""
    from libs.dsl_parser import validate_dsl
    ok, msg = validate_dsl(body.expression, macros=await _tenant_macros(db, current_user.tenant_id))
    return {"valid": ok, "message": msg or "DSL válido"}


@router.get("/rules")
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
            "weight": float(r.weight or 0.5), "version": r.version, "created_at": r.created_at,
        }
        for r in rules
    ]


@router.post("/rules", status_code=201)
async def create_rule(
    body: RuleCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    from libs.dsl_parser import validate_dsl
    ok, msg = validate_dsl(body.condition_dsl, macros=await _tenant_macros(db, current_user.tenant_id))
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
        weight=body.weight,
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await write_audit(db, current_user.tenant_id, current_user.id, "CREATE", "RuleDefinition", rule.id, after=body.model_dump())
    await db.commit()
    await db.refresh(rule)
    return {"id": rule.id, "name": rule.name, "status": rule.status}


@router.get("/rules/{rule_id}")
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
        "weight": float(r.weight or 0.5), "version": r.version, "description": r.description, "created_at": r.created_at,
    }


@router.put("/rules/{rule_id}")
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
        ok, msg = validate_dsl(body.condition_dsl, macros=await _tenant_macros(db, current_user.tenant_id))
        if not ok:
            raise HTTPException(400, detail=f"DSL inválido: {msg}")
        r.condition_dsl = body.condition_dsl
        r.version += 1
    if body.name:
        r.name = body.name
    if body.description:
        r.description = body.description
    if body.status:
        r.status = body.status
    if body.severity:
        r.severity = body.severity
    if body.params is not None:
        r.params = body.params
    if body.weight is not None:
        r.weight = body.weight
    r.updated_by = current_user.id
    await write_audit(db, current_user.tenant_id, current_user.id, "UPDATE", "RuleDefinition", rule_id, before=before, after=body.model_dump())
    await db.commit()
    return {"id": r.id, "version": r.version, "status": r.status}


@router.delete("/rules/{rule_id}")
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


@router.post("/rules/{rule_id}/simulate")
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

    if body.from_date and body.to_date and body.from_date > body.to_date:
        raise HTTPException(400, "Parâmetro 'from' não pode ser maior que 'to'")

    macros = await _tenant_macros(db, current_user.tenant_id)
    results = []
    if body.events:
        for evt in body.events:
            try:
                ctx = {
                    "transaction": evt.get("transaction", evt),
                    "bet":         evt.get("bet", {}),
                    "player":      evt.get("player", {}),
                    "features":    evt.get("features", {}),
                    "params":      r.params,
                }
                matched = eval_dsl(r.condition_dsl, ctx, macros=macros)
            except Exception as e:
                results.append({"matched": False, "error": str(e), "event": evt})
                continue
            results.append({"matched": matched, "event": evt})
        match_count = sum(1 for res in results if res.get("matched"))
        return {
            "rule_id": rule_id,
            "results": results,
            "matches": match_count,
            "total_alerts": match_count,
            "players": [],
            "false_positive_estimated": None,
            "precision_estimated": None,
            "recall_estimated": None,
            "performance_score": None,
            "timeline": [],
        }

    stmt = select(Alert).where(
        Alert.tenant_id == current_user.tenant_id,
        Alert.rule_id == rule_id,
    )
    if body.from_date:
        stmt = stmt.where(Alert.created_at >= datetime.combine(body.from_date, datetime.min.time(), tzinfo=UTC))
    if body.to_date:
        stmt = stmt.where(Alert.created_at < datetime.combine(body.to_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC))
    if body.player_ids:
        stmt = stmt.where(Alert.player_id.in_(body.player_ids))

    alerts = (await db.execute(stmt.order_by(Alert.created_at.asc()))).scalars().all()
    labeled = [a for a in alerts if a.label in {"TRUE_POSITIVE", "FALSE_POSITIVE"}]
    tp_count = len([a for a in labeled if a.label == "TRUE_POSITIVE"])
    fp_count = len([a for a in labeled if a.label == "FALSE_POSITIVE"])
    labeled_count = len(labeled)
    precision = (tp_count / labeled_count) if labeled_count else None
    false_positive_rate = (fp_count / labeled_count) if labeled_count else None

    recall_stmt = select(func.count(Alert.id)).where(
        Alert.tenant_id == current_user.tenant_id,
        Alert.label == "TRUE_POSITIVE",
    )
    if body.from_date:
        recall_stmt = recall_stmt.where(Alert.created_at >= datetime.combine(body.from_date, datetime.min.time(), tzinfo=UTC))
    if body.to_date:
        recall_stmt = recall_stmt.where(Alert.created_at < datetime.combine(body.to_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC))
    if body.player_ids:
        recall_stmt = recall_stmt.where(Alert.player_id.in_(body.player_ids))
    total_positive_labels = int((await db.execute(recall_stmt)).scalar() or 0)
    recall = (tp_count / total_positive_labels) if total_positive_labels else None

    timeline_counts: dict[str, int] = defaultdict(int)
    player_ids = set()
    for alert in alerts:
        if alert.player_id:
            player_ids.add(str(alert.player_id))
        timeline_counts[alert.created_at.date().isoformat()] += 1

    perf_parts = [v for v in (precision, recall) if v is not None]
    performance_score = (sum(perf_parts) / len(perf_parts)) if perf_parts else None
    return {
        "rule_id": rule_id,
        "results": [],
        "matches": len(alerts),
        "total_alerts": len(alerts),
        "players": sorted(player_ids),
        "false_positive_estimated": false_positive_rate,
        "precision_estimated": precision,
        "recall_estimated": recall,
        "performance_score": performance_score,
        "timeline": [
            {"date": date_key, "alerts": count}
            for date_key, count in sorted(timeline_counts.items())
        ],
    }
