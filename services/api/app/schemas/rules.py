import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.models.rule import RuleScope, RuleSeverity, RuleStatus


class RuleCreateRequest(BaseModel):
    name: str
    description: str = ""
    severity: RuleSeverity
    scope: RuleScope
    condition_dsl: str
    params: dict[str, Any] = {}
    status: RuleStatus = RuleStatus.DRAFT


class RuleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[RuleSeverity] = None
    scope: Optional[RuleScope] = None
    condition_dsl: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    status: Optional[RuleStatus] = None


class RuleResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    status: RuleStatus
    severity: RuleSeverity
    scope: RuleScope
    condition_dsl: str
    params: dict[str, Any]
    version: int
    created_by: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RuleListResponse(BaseModel):
    items: list[RuleResponse]
    total: int
    page: int
    size: int


class SimulateRequest(BaseModel):
    events: list[dict[str, Any]]


class SimulateResult(BaseModel):
    event_index: int
    matched: bool
    error: Optional[str] = None


class SimulateResponse(BaseModel):
    rule_id: uuid.UUID
    results: list[SimulateResult]
