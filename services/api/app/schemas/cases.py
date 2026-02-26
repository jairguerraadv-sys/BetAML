import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.models.case import CaseEventType, CaseStatus, ExportFormat


class CaseCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    player_id: str


class CaseUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[CaseStatus] = None


class CaseAssignRequest(BaseModel):
    user_id: uuid.UUID


class CaseEventCreateRequest(BaseModel):
    event_type: CaseEventType
    content: Optional[str] = None
    event_metadata: Optional[dict[str, Any]] = None


class ReportPackageRequest(BaseModel):
    player_data: dict[str, Any]
    events_data: dict[str, Any]
    rules_data: dict[str, Any]
    analyst_justification: str
    export_format: ExportFormat = ExportFormat.JSON


class CaseEventResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    user_id: uuid.UUID
    event_type: CaseEventType
    content: Optional[str]
    event_metadata: Optional[dict[str, Any]]
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    uploaded_by: uuid.UUID
    file_name: str
    file_path: str
    file_size: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportPackageResponse(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    tenant_id: uuid.UUID
    generated_by: uuid.UUID
    player_data: dict[str, Any]
    events_data: dict[str, Any]
    rules_data: dict[str, Any]
    analyst_justification: str
    export_format: ExportFormat
    payload: dict[str, Any]
    generated_at: datetime

    model_config = {"from_attributes": True}


class CaseResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    description: Optional[str]
    status: CaseStatus
    assigned_to: Optional[uuid.UUID]
    player_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseDetailResponse(CaseResponse):
    events: list[CaseEventResponse] = []
    evidence_files: list[EvidenceResponse] = []
    report_packages: list[ReportPackageResponse] = []


class CaseListResponse(BaseModel):
    items: list[CaseResponse]
    total: int
    page: int
    size: int
