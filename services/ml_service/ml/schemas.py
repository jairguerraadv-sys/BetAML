import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Training ──────────────────────────────────────────────────────────────────

class TrainRequest(BaseModel):
    tenant_id: uuid.UUID
    dataset_window_days: int = 90


class TrainResponse(BaseModel):
    model_id: uuid.UUID
    version: str
    metrics: dict[str, Any]
    artifact_path: str


# ── Model registry ────────────────────────────────────────────────────────────

class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    model_version: str
    algorithm: str
    trained_at: datetime
    dataset_window_days: int
    artifact_path: str
    metrics: dict[str, Any]
    feature_names: list[str]
    is_active: bool
    created_at: datetime


# ── Scoring ───────────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    tenant_id: uuid.UUID
    player_id: uuid.UUID
    features: dict[str, float]


class DriverDetail(BaseModel):
    feature: str
    value: float
    deviation: float


class ScoreResponse(BaseModel):
    anomaly_score: float
    is_anomaly: bool
    top_drivers: list[DriverDetail]
    model_version: str | None


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
