import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RuleStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DRAFT = "DRAFT"


class RuleSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RuleScope(str, enum.Enum):
    TRANSACTION = "TRANSACTION"
    BET = "BET"
    PLAYER = "PLAYER"
    DEVICE_EVENT = "DEVICE_EVENT"


class RuleDefinition(Base):
    __tablename__ = "rule_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[RuleStatus] = mapped_column(Enum(RuleStatus), nullable=False, default=RuleStatus.DRAFT)
    severity: Mapped[RuleSeverity] = mapped_column(Enum(RuleSeverity), nullable=False, default=RuleSeverity.MEDIUM)
    scope: Mapped[RuleScope] = mapped_column(Enum(RuleScope), nullable=False)
    condition_dsl: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    execution_logs: Mapped[list["RuleExecutionLog"]] = relationship(
        "RuleExecutionLog", back_populates="rule"
    )


class RuleExecutionLog(Base):
    __tablename__ = "rule_execution_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rule_definitions.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    player_id: Mapped[str] = mapped_column(String, nullable=False)
    matched: Mapped[bool] = mapped_column(nullable=False, default=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    rule: Mapped["RuleDefinition"] = relationship("RuleDefinition", back_populates="execution_logs")
