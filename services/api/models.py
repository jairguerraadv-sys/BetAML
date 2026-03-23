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
    cnpj                 = Column(String(14))
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
    refresh_token_jti = Column(Text)
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
    risk_band               = Column(String(10), nullable=False, default="LOW")  # LOW / MEDIUM / HIGH
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

    id                   = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id            = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name                 = Column(Text, nullable=False)
    description          = Column(Text)
    status               = Column(String(20), nullable=False, default="ACTIVE")
    # Modelo canônico unificado (garante compatibilidade com rules_engine)
    operator             = Column(String(10), nullable=False, default="AND")     # AND / OR / N_OF_M
    n_threshold          = Column(Integer)                                        # para N_OF_M
    child_rule_ids       = Column(JSONB, nullable=False, default=[])              # alias legado
    severity_mode        = Column(String(10), nullable=False, default="MAX")     # MAX / FIXED / WEIGHTED
    fixed_severity       = Column(String(10))
    # Campos usados pelo rules_engine via SQL
    logic                = Column(String(10))                                     # AND / OR / N_OF_M (sinônimo de operator)
    component_rule_ids   = Column(JSONB, default=[])                              # lista de rule_definition IDs
    score_weights        = Column(JSONB, default={})                              # {rule_id: weight}
    min_score_threshold  = Column(Numeric(5, 4))
    is_active            = Column(Boolean, nullable=False, default=True)
    version              = Column(Integer, nullable=False, default=1)
    created_by           = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_by           = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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

    @property
    def expression(self):
        """Alias for body_dsl — used by RuleMacroOut schema (from_attributes=True)."""
        return self.body_dsl


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
    player_list_id     = Column(UUID(as_uuid=False), ForeignKey("player_lists.id", ondelete="CASCADE"))  # alias para compatibilidade
    tenant_id          = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id          = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    external_player_id = Column(Text)
    cpf_hash           = Column(Text)
    # `value` é o campo usado pelo rules_engine para is_in_list(); pode ser CPF hash, external_id, etc.
    value              = Column(Text)
    value_type         = Column(Text)  # CPF_HASH / EXTERNAL_ID / CUSTOM
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
    label_note       = Column(Text)
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
    assigned_to          = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_by           = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_by            = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    closed_at            = Column(DateTime(timezone=True))
    auto_created         = Column(Boolean, nullable=False, default=False)  # criado automaticamente pelo sistema
    auto_created_reason  = Column(Text)   # ex: 'scoring.alerts: score=0.92, severity=CRITICAL'
    source_alert_id      = Column(UUID(as_uuid=False), ForeignKey("alerts.id", use_alter=True, name="fk_cases_source_alert"))
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


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
    pii_accessed = Column(Text)
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
    # artifact_path = nome canônico no ORM; artifact_uri = alias usado pelo ml_service (ambos mapeiam para a mesma coluna via migration)
    artifact_path        = Column(Text)       # caminho/URI do artefato (pode ser MinIO path)
    artifact_uri         = Column(Text)       # alias usado pelo ml_service (mesmo dado)
    dataset_window_start = Column(DateTime(timezone=True))
    dataset_window_end   = Column(DateTime(timezone=True))
    dataset_window_days  = Column(Integer)
    sample_count         = Column(Integer)    # alias legado
    training_rows        = Column(Integer)    # usado pelo ml_service
    feature_columns      = Column(JSONB, default=[])   # lista de features usadas no treino
    metrics              = Column(JSONB, nullable=False, default={})
    active               = Column(Boolean, nullable=False, default=False)   # alias legado
    is_active            = Column(Boolean, nullable=False, default=False)   # usado pelo ml_service
    status               = Column(String(20), nullable=False, default="STAGING")
    is_challenger        = Column(Boolean, nullable=False, default=False)
    champion_id          = Column(UUID(as_uuid=False), ForeignKey("model_registry.id"))
    promoted_by          = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    promoted_at          = Column(DateTime(timezone=True))
    trained_by           = Column(Text)   # usuário/serviço que treinou
    trained_at           = Column(DateTime(timezone=True), server_default=func.now())
    created_at           = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def version(self):
        """Alias for model_version — used by ModelRegistryOut schema (from_attributes=True)."""
        return self.model_version


class ModelInferenceLog(Base):
    __tablename__ = "model_inference_logs"

    id            = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id     = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id     = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    model_id      = Column(UUID(as_uuid=False), ForeignKey("model_registry.id", ondelete="SET NULL"))
    model_variant = Column(String(20), nullable=False, default="champion")
    anomaly_score = Column(Numeric(7, 4), nullable=False, default=0.0)
    is_anomaly    = Column(Boolean, nullable=False, default=False)
    request_id    = Column(Text)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id       = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id       = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    feature_date    = Column(Date, nullable=False)
    snapshot_date   = Column(Date)
    features        = Column(JSONB, nullable=False, default={})
    drift_score     = Column(Numeric(5, 4))
    feature_version = Column(Integer, nullable=False, default=2)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def snapshot_date_value(self):
        return self.snapshot_date or self.feature_date


# ── Admin / Config ────────────────────────────────────────────────────────────

class ScoringConfig(Base):
    __tablename__ = "scoring_configs"

    id                          = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id                   = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    rule_weight                 = Column(Numeric(4, 3), nullable=False, default=0.4)
    ml_weight                   = Column(Numeric(4, 3), nullable=False, default=0.4)
    network_weight              = Column(Numeric(4, 3), nullable=False, default=0.2)
    auto_case_threshold             = Column(Numeric(5, 4), nullable=False, default=0.75)
    # Thresholds de banda de risco (configuráveis por tenant)
    risk_band_low_threshold         = Column(Numeric(5, 4), nullable=False, default=0.35)  # abaixo disso → LOW
    risk_band_high_threshold        = Column(Numeric(5, 4), nullable=False, default=0.70)  # acima disso → HIGH
    # Compatibilidade renda/volume
    income_volume_ratio_threshold   = Column(Numeric(5, 2), nullable=False, default=1.5)   # ex: 1.5x renda mensal
    sla_critical_hours              = Column(Integer, nullable=False, default=4)
    sla_high_hours                  = Column(Integer, nullable=False, default=24)
    sla_medium_hours                = Column(Integer, nullable=False, default=72)
    sla_low_hours                   = Column(Integer, nullable=False, default=168)
    ingest_rate_limit_tpm           = Column(Integer, nullable=False, default=1000)
    ml_challenger_pct               = Column(Integer, nullable=False, default=0)
    data_retention_raw_years        = Column(Integer, nullable=False, default=5)
    data_retention_silver_years     = Column(Integer, nullable=False, default=5)
    data_retention_gold_years       = Column(Integer, nullable=False, default=3)
    # Alert severity thresholds (score 0-100)
    low_threshold               = Column(Numeric(5, 2), nullable=False, default=30.0)
    medium_threshold            = Column(Numeric(5, 2), nullable=False, default=60.0)
    high_threshold              = Column(Numeric(5, 2), nullable=False, default=80.0)
    critical_threshold          = Column(Numeric(5, 2), nullable=False, default=95.0)
    is_active                   = Column(Boolean, nullable=False, default=True)
    data_retention_days         = Column(Integer, nullable=False, default=1825)
    updated_by                  = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    created_at                  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"

    id             = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id      = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id        = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    type           = Column(String(30), nullable=False)
    title          = Column(Text, nullable=False)
    body           = Column(Text)
    is_read        = Column(Boolean, nullable=False, default=False)
    read_at        = Column(DateTime(timezone=True))
    reference_type = Column(Text)   # e.g. "alert" | "case" (added by migration_v9)
    reference_id   = Column(Text)   # UUID of the referenced entity
    created_at     = Column(DateTime(timezone=True), server_default=func.now())


class ExternalValidationRequest(Base):
    __tablename__ = "external_validation_requests"

    id                  = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id           = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id           = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    provider            = Column(String(40), nullable=False, default="mock_identity")
    validation_type     = Column(String(40), nullable=False, default="CPF_IDENTITY")
    status              = Column(String(20), nullable=False, default="PENDING")  # PENDING/COMPLETED/FAILED
    request_payload     = Column(JSONB, nullable=False, default={})
    response_payload    = Column(JSONB, default={})
    external_request_id = Column(Text)
    error_message       = Column(Text)
    requested_by        = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    requested_at        = Column(DateTime(timezone=True), server_default=func.now())
    completed_at        = Column(DateTime(timezone=True))
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SystemFlag(Base):
    __tablename__ = "system_flags"

    key        = Column(Text, primary_key=True)
    value      = Column(JSONB, nullable=False, default=None)
    updated_by = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Eventos de Negócio (OLTP) ─────────────────────────────────────────────────

class FinancialTransaction(Base):
    """Tabela OLTP para transações financeiras ingeridas.
    Permite que analistas consultem transações individuais durante investigações,
    complementando os dados agregados do ClickHouse (Gold layer).
    """
    __tablename__ = "financial_transactions"

    id                 = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id          = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id          = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    external_tx_id     = Column(Text)
    source_system      = Column(Text, nullable=False)
    type               = Column(Text, nullable=False)           # DEPOSIT, WITHDRAWAL, CHARGEBACK, BONUS
    amount             = Column(Numeric(15, 2), nullable=False)
    currency           = Column(Text, nullable=False, default="BRL")
    status             = Column(String(20), nullable=False, default="PENDING")
    payment_method     = Column(Text)
    payment_instrument = Column(Text)                          # token hash do instrumento
    bank_account_hash  = Column(Text)                          # SHA-256 do IBAN/conta
    source_event_id    = Column(Text)
    ingest_job_id      = Column(UUID(as_uuid=False), ForeignKey("ingest_jobs.id", ondelete="SET NULL"))
    raw_payload        = Column(JSONB, default={})
    occurred_at        = Column(DateTime(timezone=True), nullable=False)
    settled_at         = Column(DateTime(timezone=True))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())


class Bet(Base):
    """Tabela OLTP para apostas ingeridas."""
    __tablename__ = "bets"

    id               = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id        = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id        = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    external_bet_id  = Column(Text)
    source_system    = Column(Text, nullable=False)
    bet_type         = Column(Text, nullable=False, default="SPORTS")
    stake_amount     = Column(Numeric(15, 2), nullable=False)
    potential_payout = Column(Numeric(15, 2))
    actual_payout    = Column(Numeric(15, 2))
    odds             = Column(Numeric(10, 4))
    currency         = Column(Text, nullable=False, default="BRL")
    status           = Column(String(20), nullable=False, default="OPEN")
    event_name       = Column(Text)
    market_name      = Column(Text)
    selection_name   = Column(Text)
    source_event_id  = Column(Text)
    ingest_job_id    = Column(UUID(as_uuid=False), ForeignKey("ingest_jobs.id", ondelete="SET NULL"))
    raw_payload      = Column(JSONB, default={})
    occurred_at      = Column(DateTime(timezone=True), nullable=False)
    settled_at       = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())


class DeviceEvent(Base):
    """Tabela OLTP para eventos de dispositivo (logins, sessões)."""
    __tablename__ = "device_events"

    id              = Column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    tenant_id       = Column(UUID(as_uuid=False), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    player_id       = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="SET NULL"))
    external_evt_id = Column(Text)
    source_system   = Column(Text, nullable=False)
    action          = Column(Text, nullable=False)              # LOGIN, LOGOUT, DEPOSIT_ATTEMPT
    device_id       = Column(Text)
    device_type     = Column(Text)                             # MOBILE_IOS, DESKTOP, ...
    device_hash     = Column(Text)                             # fingerprint SHA-256
    ip_address      = Column(Text)
    ip_hash         = Column(Text)                             # SHA-256 do IP
    country_code    = Column(Text)
    user_agent      = Column(Text)
    session_id      = Column(Text)
    source_event_id = Column(Text)
    ingest_job_id   = Column(UUID(as_uuid=False), ForeignKey("ingest_jobs.id", ondelete="SET NULL"))
    raw_payload     = Column(JSONB, default={})
    occurred_at     = Column(DateTime(timezone=True), nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
