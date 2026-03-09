"""routers/rules.py — CRUD de RuleDefinition + simulação DSL."""
from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from models import RuleDefinition, User
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


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    condition_dsl: Optional[str] = None
    params: Optional[dict[str, Any]] = None


class SimulateRequest(BaseModel):
    events: list[dict[str, Any]]


class ValidateDSLRequest(BaseModel):
    expression: str


@router.post("/rules/validate")
async def validate_rule_dsl(
    body: ValidateDSLRequest,
    current_user: User = Depends(get_current_user),
):
    """Valida sintaxe de uma expressão DSL sem persistir."""
    from libs.dsl_parser import validate_dsl
    ok, msg = validate_dsl(body.expression)
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
            "version": r.version, "created_at": r.created_at,
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
        "version": r.version, "description": r.description, "created_at": r.created_at,
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
            results.append({"matched": False, "error": str(e), "event": evt})
            continue
        results.append({"matched": matched, "event": evt})
    return {
        "rule_id": rule_id,
        "results": results,
        "matches": sum(1 for res in results if res.get("matched")),
    }
