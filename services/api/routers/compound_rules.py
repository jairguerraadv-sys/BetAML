"""routers/compound_rules.py — Compound rules and DSL macros (M3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user
from database import get_db
from libs.schemas import CompoundRuleOut, RuleMacroOut
from models import CompoundRule, RuleMacro, User
from utils import write_audit

router = APIRouter(tags=["rules"])


# ── Pydantic in (create) ───────────────────────────────────────────────────────

class CompoundRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    logic: str = Field("AND", max_length=10)
    component_rule_ids: list[str]
    score_weights: dict | None = None
    min_score_threshold: float | None = None


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
    return result.scalars().all()


@router.post("/rules/compound", status_code=201, response_model=CompoundRuleOut)
async def create_compound_rule(
    body: CompoundRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a compound rule that combines multiple component rules."""
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
