"""Pydantic v2 models for AML workflow objects (alerts, cases, reports)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AlertType(str, Enum):
    RULE = "RULE"
    ANOMALY = "ANOMALY"
    COMPOSITE = "COMPOSITE"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_FP = "CLOSED_FP"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    CLOSED_SAR = "CLOSED_SAR"
    CLOSED_NO_ACTION = "CLOSED_NO_ACTION"


class ExportFormat(str, Enum):
    JSON = "JSON"
    CSV = "CSV"


# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------


class AlertEvidence(BaseModel):
    features: dict[str, Any]
    thresholds: dict[str, Any]
    ruleVersion: str
    mlScore: Optional[float] = None
    topDrivers: Optional[list[Any]] = None


class AlertSchema(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tenantId: UUID
    playerId: str
    ruleId: Optional[str] = None
    alertType: AlertType
    severity: Severity
    status: AlertStatus
    evidence: AlertEvidence
    createdAt: datetime
    updatedAt: datetime


# ---------------------------------------------------------------------------
# Case model
# ---------------------------------------------------------------------------


class CaseSchema(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    tenantId: UUID
    title: str
    description: str
    assignedTo: Optional[str] = None
    status: CaseStatus
    alerts: list[UUID]
    createdAt: datetime
    updatedAt: datetime


# ---------------------------------------------------------------------------
# Report package model
# ---------------------------------------------------------------------------


class ReportPackageSchema(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    caseId: UUID
    tenantId: UUID
    playerData: dict[str, Any]
    events: list[Any]
    rules: list[Any]
    analystJustification: str
    generatedAt: datetime
    exportFormat: ExportFormat
