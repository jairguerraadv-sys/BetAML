import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.middleware.tenant import CurrentUser, require_admin, require_analyst_or_admin, require_any_role
from app.models.audit import AuditLog
from app.models.rule import RuleDefinition, RuleStatus
from app.schemas.rules import (
    RuleCreateRequest,
    RuleListResponse,
    RuleResponse,
    RuleUpdateRequest,
    SimulateRequest,
    SimulateResponse,
    SimulateResult,
)

try:
    from dsl.parser import DSLEvalError, DSLEvaluator, DSLParseError, DSLParser
    _DSL_AVAILABLE = True
except ImportError:
    _DSL_AVAILABLE = False

router = APIRouter(prefix="/rules", tags=["rules"])


def _parse_dsl(dsl_string: str) -> dict:
    if not _DSL_AVAILABLE:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DSL parser not available")
    parser = DSLParser()
    try:
        return parser.parse(dsl_string)
    except DSLParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"DSL parse error: {exc}")


@router.get("", response_model=RuleListResponse)
async def list_rules(
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = 1,
    size: int = 20,
    rule_status: Optional[RuleStatus] = None,
) -> RuleListResponse:
    q = select(RuleDefinition).where(RuleDefinition.tenant_id == current.tenant_id)
    if rule_status:
        q = q.where(RuleDefinition.status == rule_status)
    q = q.order_by(RuleDefinition.created_at.desc())
    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar_one()
    offset = (page - 1) * size
    result = await db.execute(q.offset(offset).limit(size))
    rules = result.scalars().all()
    return RuleListResponse(items=[RuleResponse.model_validate(r) for r in rules], total=total, page=page, size=size)


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreateRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleResponse:
    _parse_dsl(body.condition_dsl)  # validates DSL syntax

    rule = RuleDefinition(
        tenant_id=current.tenant_id,
        name=body.name,
        description=body.description,
        severity=body.severity,
        scope=body.scope,
        condition_dsl=body.condition_dsl,
        params=body.params,
        status=body.status,
        created_by=current.user.id,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="RULE_CREATED",
        entity_type="RuleDefinition",
        entity_id=str(rule.id),
        new_values={"name": rule.name, "status": rule.status.value},
    )
    db.add(audit)
    return RuleResponse.model_validate(rule)


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: uuid.UUID,
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleResponse:
    result = await db.execute(
        select(RuleDefinition).where(
            RuleDefinition.id == rule_id, RuleDefinition.tenant_id == current.tenant_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return RuleResponse.model_validate(rule)


@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdateRequest,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RuleResponse:
    result = await db.execute(
        select(RuleDefinition).where(
            RuleDefinition.id == rule_id, RuleDefinition.tenant_id == current.tenant_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if body.condition_dsl is not None:
        _parse_dsl(body.condition_dsl)

    old_values = {
        "name": rule.name,
        "status": rule.status.value,
        "version": rule.version,
    }

    update_data = body.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(rule, field, value)
    rule.version += 1

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="RULE_UPDATED",
        entity_type="RuleDefinition",
        entity_id=str(rule.id),
        old_values=old_values,
        new_values={"name": rule.name, "status": rule.status.value, "version": rule.version},
    )
    db.add(audit)
    await db.flush()
    await db.refresh(rule)
    return RuleResponse.model_validate(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    current: Annotated[CurrentUser, Depends(require_analyst_or_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(RuleDefinition).where(
            RuleDefinition.id == rule_id, RuleDefinition.tenant_id == current.tenant_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    old_status = rule.status.value
    rule.status = RuleStatus.INACTIVE

    audit = AuditLog(
        tenant_id=current.tenant_id,
        user_id=current.user.id,
        action="RULE_DELETED",
        entity_type="RuleDefinition",
        entity_id=str(rule.id),
        old_values={"status": old_status},
        new_values={"status": RuleStatus.INACTIVE.value},
    )
    db.add(audit)


@router.post("/{rule_id}/simulate", response_model=SimulateResponse)
async def simulate_rule(
    rule_id: uuid.UUID,
    body: SimulateRequest,
    current: Annotated[CurrentUser, Depends(require_any_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SimulateResponse:
    result = await db.execute(
        select(RuleDefinition).where(
            RuleDefinition.id == rule_id, RuleDefinition.tenant_id == current.tenant_id
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if not _DSL_AVAILABLE:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="DSL parser not available")

    parser = DSLParser()
    try:
        ast = parser.parse(rule.condition_dsl)
    except DSLParseError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Rule DSL error: {exc}")

    evaluator = DSLEvaluator()
    results: list[SimulateResult] = []
    for idx, event in enumerate(body.events):
        try:
            matched = evaluator.evaluate(ast, event)
            results.append(SimulateResult(event_index=idx, matched=matched))
        except (DSLEvalError, Exception) as exc:
            results.append(SimulateResult(event_index=idx, matched=False, error=str(exc)))

    return SimulateResponse(rule_id=rule_id, results=results)
