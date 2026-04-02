"""routers/compound_rules.py — Compound rules and DSL macros (M3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, get_current_user, require_roles, require_role, require_role_any
from database import get_db
from libs.schemas import CompoundRuleOut, RuleMacroOut
from models import CompoundRule, RuleDefinition, RuleMacro, User
from utils import write_audit

router = APIRouter(tags=["rules"])


# ── Pydantic in (create) ───────────────────────────────────────────────────────

class CompoundRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None
    logic: str = Field("AND", max_length=10)
    operator: str | None = Field(default=None, max_length=10)
    component_rule_ids: list[str]
    score_weights: dict | None = None
    min_score_threshold: float | None = None
    n_threshold: int | None = None
    severity_mode: str = "MAX"
    fixed_severity: str | None = None
    status: str = "ACTIVE"


class CompoundRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    logic: str | None = None
    operator: str | None = None
    component_rule_ids: list[str] | None = None
    score_weights: dict | None = None
    min_score_threshold: float | None = None
    n_threshold: int | None = None
    severity_mode: str | None = None
    fixed_severity: str | None = None
    is_active: bool | None = None
    status: str | None = None


class RuleMacroCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    expression: str
    description: str | None = None


# ── Compound Rules ────────────────────────────────────────────────────────────

@router.get("/rules/compound", response_model=list[CompoundRuleOut])
async def list_compound_rules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all compound rules for the current tenant."""
    result = await db.execute(
        select(CompoundRule).where(CompoundRule.tenant_id == current_user.tenant_id)
    )
    rules = result.scalars().all()
    # Defensive normalization: older rows or manual inserts may have null JSON fields.
    return [
        {
            "id": str(r.id),
            "tenant_id": str(r.tenant_id),
            "name": r.name,
            "logic": r.logic,
            "component_rule_ids": (r.component_rule_ids or []),
            "score_weights": (r.score_weights or {}),
            "min_score_threshold": float(r.min_score_threshold) if r.min_score_threshold is not None else None,
            "is_active": bool(getattr(r, "is_active", True)),
            "created_at": r.created_at,
        }
        for r in rules
    ]


@router.post("/rules/compound", status_code=201, response_model=CompoundRuleOut)
async def create_compound_rule(
    body: CompoundRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(AppRole.GESTOR)),
):
    """Create a compound rule that combines multiple component rules."""
    component_rows = (
        await db.execute(
            select(RuleDefinition.id).where(
                RuleDefinition.tenant_id == current_user.tenant_id,
                RuleDefinition.id.in_(body.component_rule_ids),
            )
        )
    ).scalars().all()
    if len(component_rows) != len(set(body.component_rule_ids)):
        raise HTTPException(400, "Uma ou mais regras componentes não pertencem ao tenant.")

    operator = (body.operator or body.logic or "AND").upper()
    rule = CompoundRule(
        tenant_id=current_user.tenant_id,
        name=body.name,
        description=body.description,
        status=body.status,
        logic=operator,
        n_threshold=body.n_threshold,
        severity_mode=body.severity_mode,
        fixed_severity=body.fixed_severity,
        component_rule_ids=body.component_rule_ids or [],
        score_weights=body.score_weights or {},
        min_score_threshold=body.min_score_threshold,
        is_active=body.status == "ACTIVE",
        created_by=current_user.id,
    )
    db.add(rule)
    await db.flush()
    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.id,
        action="CREATE_COMPOUND_RULE",
        entity_type="CompoundRule",
        entity_id=str(rule.id),
    )
    await db.commit()
    return rule


@router.put("/rules/compound/{rule_id}", response_model=CompoundRuleOut)
async def update_compound_rule(
    rule_id: str,
    body: CompoundRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(AppRole.GESTOR)),
):
    row = (
        await db.execute(
            select(CompoundRule).where(
                CompoundRule.id == rule_id,
                CompoundRule.tenant_id == current_user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Compound rule not found")

    before = {
        "name": row.name,
        "logic": row.logic,
        "component_rule_ids": row.component_rule_ids or [],
        "min_score_threshold": float(row.min_score_threshold) if row.min_score_threshold is not None else None,
    }

    if body.component_rule_ids is not None:
        component_rows = (
            await db.execute(
                select(RuleDefinition.id).where(
                    RuleDefinition.tenant_id == current_user.tenant_id,
                    RuleDefinition.id.in_(body.component_rule_ids),
                )
            )
        ).scalars().all()
        if len(component_rows) != len(set(body.component_rule_ids)):
            raise HTTPException(400, "Uma ou mais regras componentes não pertencem ao tenant.")
        row.component_rule_ids = body.component_rule_ids

    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.score_weights is not None:
        row.score_weights = body.score_weights
    if body.min_score_threshold is not None:
        row.min_score_threshold = body.min_score_threshold
    if body.n_threshold is not None:
        row.n_threshold = body.n_threshold
    if body.severity_mode is not None:
        row.severity_mode = body.severity_mode
    if body.fixed_severity is not None:
        row.fixed_severity = body.fixed_severity
    if body.is_active is not None:
        row.is_active = body.is_active
    if body.status is not None:
        row.status = body.status
        row.is_active = body.status == "ACTIVE"
    if body.logic is not None or body.operator is not None:
        operator = (body.operator or body.logic or row.logic or row.operator or "AND").upper()
        row.logic = operator
        row.operator = operator
    row.version = int(getattr(row, "version", 1) or 1) + 1
    row.updated_by = current_user.id

    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.id,
        action="UPDATE_COMPOUND_RULE",
        entity_type="CompoundRule",
        entity_id=str(row.id),
        before=before,
        after=body.model_dump(exclude_none=True),
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/rules/compound/{rule_id}", status_code=204)
async def delete_compound_rule(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a compound rule."""
    row = (await db.execute(
        select(CompoundRule).where(
            CompoundRule.id == rule_id,
            CompoundRule.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Compound rule not found")
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "DELETE_COMPOUND_RULE", "CompoundRule", rule_id,
        before={"name": row.name},
    )
    await db.delete(row)
    await db.commit()


# ── Rule Macros ───────────────────────────────────────────────────────────────

@router.get("/rules/macros", response_model=list[RuleMacroOut])
async def list_macros(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all DSL macros for the current tenant."""
    result = await db.execute(
        select(RuleMacro).where(RuleMacro.tenant_id == current_user.tenant_id)
    )
    return result.scalars().all()


@router.post("/rules/macros", status_code=201, response_model=RuleMacroOut)
async def create_macro(
    body: RuleMacroCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a DSL macro (reusable named expression)."""
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
    await write_audit(
        db,
        tenant_id=current_user.tenant_id,
        actor_id=current_user.id,
        action="CREATE_MACRO",
        entity_type="RuleMacro",
        entity_id=str(macro.id),
    )
    await db.commit()
    return macro


@router.delete("/rules/macros/{macro_id}", status_code=204)
async def delete_macro(
    macro_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a DSL macro."""
    row = (await db.execute(
        select(RuleMacro).where(
            RuleMacro.id == macro_id,
            RuleMacro.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Macro not found")
    await db.delete(row)
    await db.commit()
