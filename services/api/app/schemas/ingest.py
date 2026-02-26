import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from app.models.ingest import EntityType, IngestStatus


class IngestFileResponse(BaseModel):
    job_id: uuid.UUID
    status: IngestStatus


class IngestEventRequest(BaseModel):
    source_system: str
    source_event_id: str
    entity_type: EntityType
    occurred_at: datetime
    payload: dict[str, Any]


class IngestEventResponse(BaseModel):
    event_id: str


class IngestBatchRequest(BaseModel):
    events: list[IngestEventRequest]


class IngestBatchResponse(BaseModel):
    count: int
    event_ids: list[str]


class IngestJobResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    source_system: str
    mapping_config_id: Optional[uuid.UUID]
    status: IngestStatus
    file_name: Optional[str]
    record_count: Optional[int]
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IngestJobListResponse(BaseModel):
    items: list[IngestJobResponse]
    total: int
