import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.models.alert import AlertSeverity, AlertStatus, AlertType


class AlertResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    player_id: str
    player_cpf: Optional[str]
    rule_id: Optional[uuid.UUID]
    alert_type: AlertType
    severity: AlertSeverity
    status: AlertStatus
    evidence: dict[str, Any]
    risk_score: Optional[float]
    case_id: Optional[uuid.UUID]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int
    page: int
    size: int


class TriageRequest(BaseModel):
    note: str


class CloseAlertRequest(BaseModel):
    status: AlertStatus  # CLOSED_TP or CLOSED_FP
    note: str


class LinkToCaseRequest(BaseModel):
    case_id: uuid.UUID
