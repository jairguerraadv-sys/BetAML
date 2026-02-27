"""SQLAlchemy ORM models (OLTP — PostgreSQL) — BetAML v2."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime,
    ForeignKey, Integer, LargeBinary, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from database import Base


def _uuid():
    return str(uuid.uuid4())


# ── Core ──────────────────────────────────────────────────────────────────────

class Tenant(Base):
    __tablename__ = "tenants"

    id                   = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name                 = Column(Text, nullable=False, unique=True)
    slug                 = Column(Text, nullable=False, unique=True)
    active               = Column(Boolean, nullable=False, default=True)
    settings             = Column(JSONB, nullable=False, default={})
    risk_score_threshold = Column(Numeric(5, 2), nullable=False, default=0.75)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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

    id                      = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id               = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    external_player_id      = Column(Text, nullable=False)
    external_id             = Column(Text)
    full_name               = Column(Text)
    cpf_encrypted           = Column(LargeBinary, nullable=False)
    name_encrypted          = Column(LargeBinary, nullable=False)
    birth_date              = Column(Date)
    pep_flag                = Column(Boolean, nullable=False, default=False)
    declared_income_monthly = Column(Numeric(15, 2))
    profession              = Column(Text)
    status                  = Column(String(20), nullable=False, default="ACTIVE")
    registered_since        = Column(Date)
    risk_score              = Column(Numeric(5, 4), nullable=False, default=0.0)
    last_scored_at          = Column(DateTime(timezone=True))
    created_at              = Column(DateTime(timezone=True), server_default=func.now())
    updated_at              = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Ingest ────────────────────────────────────────────────────────────────────

class MappingConfig(Base):
    __tablename__ = "mapping_configs"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id      = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name           = Column(Text, nullable=False)
    source_system  = Column(Text, nullable=False)
    entity_type    = Column(Text, nullable=False)
    version        = Column(Text, nullable=False, default="1.0")
    version_number = Column(Integer, nullable=False, default=1)
    is_current     = Column(Boolean, nullable=False, default=True)
    parent_id      = Column(UUID(as_uuid=False), ForeignKey("mapping_configs.id"))
    change_notes   = Column(Text)
    config_json    = Column(JSONB, nullable=False)
    active         = Column(Boolean, nullable=False, default=True)
    created_by     = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id                 = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id          = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    source_system      = Column(Text, nullable=False)
    mapping_config_id  = Column(UUID(as_uuid=False), ForeignKey("mapping_configs.id"))
    mapping_version_id = Column(UUID(as_uuid=False), ForeignKey("mapping_configs.id"))
    connector_type     = Column(String(20), nullable=False, default="FILE")
    file_name          = Column(Text)
    file_size_bytes    = Column(BigInteger)
    file_path          = Column(Text)
    bytes_processed    = Column(BigInteger, default=0)
    duration_ms        = Column(BigInteger)
    status             = Column(String(20), nullable=False, default="QUEUED")
    total_records      = Column(Integer)
    processed_records  = Column(Integer, default=0)
    failed_records     = Column(Integer, default=0)
    error_message      = Column(Text)
    error_sample       = Column(JSONB, default=[])
    reprocessed_from   = Column(UUID(as_uuid=False), ForeignKey("ingest_jobs.id"))
    created_by         = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IngestError(Base):
    __tablename__ = "ingest_errors"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    ingest_job_id = Column(UUID(as_uuid=False), ForeignKey("ingest_jobs.id", ondelete="SET NULL"))
    source_system = Column(Text, nullable=False)
    entity_type   = Column(Text)
    raw_payload   = Column(Text, nullable=False)
    error_reason  = Column(Text, nullable=False)
    error_detail  = Column(JSONB, nullable=False, default={})
    line_number   = Column(Integer)
    resolved      = Column(Boolean, nullable=False, default=False)
    resolved_by   = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    resolved_at   = Column(DateTime(timezone=True))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name          = Column(Text, nullable=False)
    key_hash      = Column(Text, nullable=False, unique=True)
    key_prefix    = Column(Text, nullable=False)
    source_system = Column(Text)
    permissions   = Column(JSONB, nullable=False, default=["ingest"])
    active        = Column(Boolean, nullable=False, default=True)
    last_used_at  = Column(DateTime(timezone=True))
    expires_at    = Column(DateTime(timezone=True))
    created_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    revoked_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    revoked_at    = Column(DateTime(timezone=True))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


# ── Rules ─────────────────────────────────────────────────────────────────────

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
    weight        = Column(Numeric(4, 3), nullable=False, default=0.5)
    version       = Column(Integer, nullable=False, default=1)
    created_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_by    = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CompoundRule(Base):
    __tablename__ = "compound_rules"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id      = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name           = Column(Text, nullable=False)
    description    = Column(Text)
    status         = Column(String(20), nullable=False, default="ACTIVE")
    operator       = Column(String(10), nullable=False, default="AND")
    n_threshold    = Column(Integer)
    child_rule_ids = Column(JSONB, nullable=False, default=[])
    severity_mode  = Column(String(10), nullable=False, default="MAX")
    fixed_severity = Column(String(10))
    version        = Column(Integer, nullable=False, default=1)
    created_by     = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_by     = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RuleMacro(Base):
    __tablename__ = "rule_macros"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name        = Column(Text, nullable=False)
    body_dsl    = Column(Text, nullable=False)
    description = Column(Text)
    created_by  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Player Lists ──────────────────────────────────────────────────────────────

class PlayerList(Base):
    __tablename__ = "player_lists"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name        = Column(Text, nullable=False)
    list_type   = Column(String(20), nullable=False)
    description = Column(Text)
    active      = Column(Boolean, nullable=False, default=True)
    source      = Column(String(20), nullable=False, default="MANUAL")
    created_by  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_by  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlayerListEntry(Base):
    __tablename__ = "player_list_entries"

    id                 = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    list_id            = Column(UUID(as_uuid=False), ForeignKey("player_lists.id", ondelete="CASCADE"), nullable=False)
    tenant_id          = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id          = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    external_player_id = Column(Text)
    cpf_hash           = Column(Text)
    notes              = Column(Text)
    added_by           = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    added_at           = Column(DateTime(timezone=True), server_default=func.now())


# ── Alerts / Cases ────────────────────────────────────────────────────────────

class Alert(Base):
    __tablename__ = "alerts"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id        = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id        = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    rule_id          = Column(UUID(as_uuid=False), ForeignKey("rule_definitions.id"))
    compound_rule_id = Column(UUID(as_uuid=False), ForeignKey("compound_rules.id"))
    alert_type       = Column(String(20), nullable=False, default="RULE")
    severity         = Column(String(20), nullable=False)
    status           = Column(String(20), nullable=False, default="OPEN")
    title            = Column(Text, nullable=False)
    description      = Column(Text)
    evidence         = Column(JSONB, nullable=False, default={})
    anomaly_score    = Column(Numeric(5, 4))
    composite_score  = Column(Numeric(5, 4))
    score_breakdown  = Column(JSONB, default={})
    rule_weight      = Column(Numeric(4, 3), default=0.4)
    ml_weight        = Column(Numeric(4, 3), default=0.4)
    network_weight   = Column(Numeric(4, 3), default=0.2)
    source_event_id  = Column(Text)
    case_id          = Column(UUID(as_uuid=False), ForeignKey("cases.id", use_alter=True, name="fk_alerts_case"))
    label            = Column(String(20))
    labeled_by       = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    labeled_at       = Column(DateTime(timezone=True))
    triaged_by       = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    triaged_at       = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Case(Base):
    __tablename__ = "cases"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id        = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id        = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    reference_number = Column(Text)
    title            = Column(Text, nullable=False)
    description      = Column(Text)
    status           = Column(String(30), nullable=False, default="OPEN")
    severity         = Column(String(20), nullable=False, default="HIGH")
    priority         = Column(String(20), nullable=False, default="MEDIUM")
    sla_due_at       = Column(DateTime(timezone=True))
    assigned_to      = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_by       = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_by        = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_at        = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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

    id                = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id         = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    case_id           = Column(UUID(as_uuid=False), ForeignKey("cases.id"), nullable=False)
    player_id         = Column(UUID(as_uuid=False), ForeignKey("players.id"))
    payload           = Column(JSONB, nullable=False)
    format            = Column(String(10), nullable=False, default="JSON")
    pdf_path          = Column(Text)
    status            = Column(String(20), nullable=False, default="DRAFT")
    analyst_narrative = Column(Text)
    decision          = Column(String(20))
    created_by        = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at        = Column(DateTime(timezone=True), server_default=func.now())


# ── Logs & Audit ──────────────────────────────────────────────────────────────

class RuleExecutionLog(Base):
    __tablename__ = "rule_execution_logs"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id        = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    rule_id          = Column(UUID(as_uuid=False), ForeignKey("rule_definitions.id"), nullable=False)
    rule_version     = Column(Integer, nullable=False)
    source_event_id  = Column(Text, nullable=False)
    player_id        = Column(UUID(as_uuid=False))
    matched          = Column(Boolean, nullable=False)
    evaluation_ms    = Column(Integer)
    context_snapshot = Column(JSONB)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())


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


# ── ML ────────────────────────────────────────────────────────────────────────

class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id                   = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id            = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    model_name           = Column(Text, nullable=False)
    model_type           = Column(String(20), nullable=False, default="ANOMALY")
    model_version        = Column(Text, nullable=False)
    algorithm            = Column(Text, nullable=False)
    artifact_path        = Column(Text, nullable=False)
    dataset_window_start = Column(DateTime(timezone=True))
    dataset_window_end   = Column(DateTime(timezone=True))
    dataset_window_days  = Column(Integer)
    sample_count         = Column(Integer)
    metrics              = Column(JSONB, nullable=False, default={})
    active               = Column(Boolean, nullable=False, default=False)
    status               = Column(String(20), nullable=False, default="STAGING")
    is_challenger        = Column(Boolean, nullable=False, default=False)
    champion_id          = Column(UUID(as_uuid=False), ForeignKey("model_registry.id"))
    promoted_by          = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    promoted_at          = Column(DateTime(timezone=True))
    trained_at           = Column(DateTime(timezone=True), server_default=func.now())
    created_at           = Column(DateTime(timezone=True), server_default=func.now())


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"

    id           = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id    = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id    = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    feature_date = Column(Date, nullable=False)
    features     = Column(JSONB, nullable=False, default={})
    drift_score  = Column(Numeric(5, 4))
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


# ── Admin / Config ────────────────────────────────────────────────────────────

class ScoringConfig(Base):
    __tablename__ = "scoring_configs"

    id                          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id                   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    rule_weight                 = Column(Numeric(4, 3), nullable=False, default=0.4)
    ml_weight                   = Column(Numeric(4, 3), nullable=False, default=0.4)
    network_weight              = Column(Numeric(4, 3), nullable=False, default=0.2)
    auto_case_threshold         = Column(Numeric(5, 4), nullable=False, default=0.75)
    sla_critical_hours          = Column(Integer, nullable=False, default=4)
    sla_high_hours              = Column(Integer, nullable=False, default=24)
    sla_medium_hours            = Column(Integer, nullable=False, default=72)
    sla_low_hours               = Column(Integer, nullable=False, default=168)
    ingest_rate_limit_tpm       = Column(Integer, nullable=False, default=1000)
    data_retention_raw_years    = Column(Integer, nullable=False, default=5)
    data_retention_silver_years = Column(Integer, nullable=False, default=5)
    data_retention_gold_years   = Column(Integer, nullable=False, default=3)
    updated_by                  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at                  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id     = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type        = Column(String(30), nullable=False)
    title       = Column(Text, nullable=False)
    body        = Column(Text)
    entity_type = Column(Text)
    entity_id   = Column(Text)
    read        = Column(Boolean, nullable=False, default=False)
    read_at     = Column(DateTime(timezone=True))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class SystemFlag(Base):
    __tablename__ = "system_flags"

    key        = Column(Text, primary_key=True)
    value      = Column(JSONB, nullable=False, default=None)
    updated_by = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
