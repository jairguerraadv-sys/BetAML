"""SQLAlchemy ORM models (OLTP — PostgreSQL)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from database import Base


def _uuid():
    return str(uuid.uuid4())


class Tenant(Base):
    __tablename__ = "tenants"

    id           = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name         = Column(Text, nullable=False, unique=True)
    slug         = Column(Text, nullable=False, unique=True)
    active       = Column(Boolean, nullable=False, default=True)
    settings     = Column(JSONB, nullable=False, default={})
    risk_score_threshold = Column(Numeric(5, 2), nullable=False, default=0.75)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    username      = Column(Text, nullable=False)
    email         = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=False)
    role          = Column(String(20), nullable=False)
    active        = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Player(Base):
    __tablename__ = "players"

    id                   = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id            = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    external_player_id   = Column(Text, nullable=False)
    cpf_encrypted        = Column(LargeBinary, nullable=False)
    name_encrypted       = Column(LargeBinary, nullable=False)
    birth_date           = Column(DateTime)
    pep_flag             = Column(Boolean, nullable=False, default=False)
    declared_income_monthly = Column(Numeric(15, 2))
    profession           = Column(Text)
    risk_score           = Column(Numeric(5, 4), nullable=False, default=0.0)
    last_scored_at       = Column(DateTime(timezone=True))
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MappingConfig(Base):
    __tablename__ = "mapping_configs"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name          = Column(Text, nullable=False)
    source_system = Column(Text, nullable=False)
    entity_type   = Column(Text, nullable=False)
    version       = Column(Text, nullable=False, default="1.0")
    config_json   = Column(JSONB, nullable=False)
    active        = Column(Boolean, nullable=False, default=True)
    created_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id        = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    source_system    = Column(Text, nullable=False)
    mapping_config_id= Column(UUID(as_uuid=False), ForeignKey("mapping_configs.id"))
    file_name        = Column(Text)
    file_size_bytes  = Column(BigInteger)
    file_path        = Column(Text)
    status           = Column(String(20), nullable=False, default="QUEUED")
    total_records    = Column(Integer)
    processed_records= Column(Integer, default=0)
    failed_records   = Column(Integer, default=0)
    error_message    = Column(Text)
    created_by       = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RuleDefinition(Base):
    __tablename__ = "rule_definitions"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name          = Column(Text, nullable=False)
    description   = Column(Text)
    status        = Column(String(20), nullable=False, default="ACTIVE")
    severity      = Column(String(20), nullable=False, default="MEDIUM")
    scope         = Column(String(20), nullable=False, default="TRANSACTION")
    condition_dsl = Column(Text, nullable=False)
    params        = Column(JSONB, nullable=False, default={})
    version       = Column(Integer, nullable=False, default=1)
    created_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id       = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id       = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    rule_id         = Column(UUID(as_uuid=False), ForeignKey("rule_definitions.id"))
    alert_type      = Column(String(20), nullable=False, default="RULE")
    severity        = Column(String(20), nullable=False)
    status          = Column(String(20), nullable=False, default="OPEN")
    title           = Column(Text, nullable=False)
    description     = Column(Text)
    evidence        = Column(JSONB, nullable=False, default={})
    anomaly_score   = Column(Numeric(5, 4))
    source_event_id = Column(Text)
    case_id         = Column(UUID(as_uuid=False), ForeignKey("cases.id", use_alter=True, name="fk_alerts_case"))
    triaged_by      = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    triaged_at      = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Case(Base):
    __tablename__ = "cases"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id   = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    title       = Column(Text, nullable=False)
    description = Column(Text)
    status      = Column(String(30), nullable=False, default="OPEN")
    severity    = Column(String(20), nullable=False, default="HIGH")
    assigned_to = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_by  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_by   = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_at   = Column(DateTime(timezone=True))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CaseEvent(Base):
    __tablename__ = "case_events"

    id         = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    case_id    = Column(UUID(as_uuid=False), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    tenant_id  = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    event_type = Column(String(30), nullable=False)
    content    = Column(JSONB, nullable=False, default={})
    created_by = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReportPackage(Base):
    __tablename__ = "report_packages"

    id         = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id  = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    case_id    = Column(UUID(as_uuid=False), ForeignKey("cases.id"), nullable=False)
    player_id  = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    payload    = Column(JSONB, nullable=False)
    format     = Column(String(10), nullable=False, default="JSON")
    created_by = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id   = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    user_id     = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    action      = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    entity_id   = Column(Text)
    before      = Column(JSONB)
    after       = Column(JSONB)
    ip_address  = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id                  = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id           = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    model_name          = Column(Text, nullable=False)
    model_version       = Column(Text, nullable=False)
    algorithm           = Column(Text, nullable=False)
    artifact_path       = Column(Text, nullable=False)
    dataset_window_days = Column(Integer)
    metrics             = Column(JSONB, nullable=False, default={})
    active              = Column(Boolean, nullable=False, default=False)
    trained_at          = Column(DateTime(timezone=True), server_default=func.now())
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
