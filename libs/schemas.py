"""
Canonical Event Envelope + payload schemas (Pydantic v2).
Shared by API, stream_processor, rules_engine, ml_service.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

_UTC = timezone.utc

# ──────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────

class EntityType(str, Enum):
    PLAYER = "PLAYER"
    TRANSACTION = "TRANSACTION"
    BET = "BET"
    DEVICE_EVENT = "DEVICE_EVENT"


class TransactionType(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    CHARGEBACK = "CHARGEBACK"
    BONUS = "BONUS"
    ADJUSTMENT = "ADJUSTMENT"


class TransactionStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class PaymentMethod(str, Enum):
    PIX = "PIX"
    TED = "TED"
    CARD = "CARD"
    WALLET = "WALLET"
    OTHER = "OTHER"


class BetChannel(str, Enum):
    WEB = "WEB"
    APP = "APP"
    TERMINAL = "TERMINAL"


# ──────────────────────────────────────────────────
# Ingest metadata
# ──────────────────────────────────────────────────

class IngestMetadata(BaseModel):
    received_at: datetime = Field(default_factory=lambda: datetime.now(_UTC))
    file_name: Optional[str] = None
    api_key_id: Optional[str] = None
    checksum: Optional[str] = None
    mapper_version: str = "1.0"
    schema_version: int = 1


# ──────────────────────────────────────────────────
# Canonical payloads (Silver)
# ──────────────────────────────────────────────────

class PlayerPayload(BaseModel):
    external_player_id: str
    cpf: str                         # armazenado criptografado no OLTP
    name: str
    birth_date: Optional[str] = None
    pep_flag: bool = False
    declared_income_monthly: Optional[Decimal] = None
    profession: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    nationality: Optional[str] = "BR"
    registration_date: Optional[datetime] = None


class PaymentInstrument(BaseModel):
    institution_code: Optional[str] = None
    holder_document: Optional[str] = None  # CPF/CNPJ do titular
    verified_flag: bool = False
    instrument_type: Optional[str] = None
    masked_number: Optional[str] = None


class TransactionPayload(BaseModel):
    external_transaction_id: Optional[str] = None
    player_id: str                   # internal UUID
    player_cpf: Optional[str] = None
    type: TransactionType
    amount: Decimal
    currency: str = "BRL"
    method: PaymentMethod
    status: TransactionStatus
    payment_instrument: Optional[PaymentInstrument] = None
    occurred_at: datetime
    description: Optional[str] = None


class BetPayload(BaseModel):
    external_bet_id: Optional[str] = None
    player_id: str
    player_cpf: Optional[str] = None
    stake_amount: Decimal
    odds: Optional[Decimal] = None
    potential_payout: Optional[Decimal] = None
    settled_payout: Optional[Decimal] = None
    market_type: Optional[str] = None
    sport: Optional[str] = None
    event_id: Optional[str] = None
    selection: Optional[str] = None
    channel: BetChannel = BetChannel.WEB
    placed_at: datetime
    settled_at: Optional[datetime] = None
    status: Optional[str] = None


class DeviceEventPayload(BaseModel):
    player_id: Optional[str] = None
    player_cpf: Optional[str] = None
    device_id: str
    ip: Optional[str] = None
    geo_country: Optional[str] = None
    user_agent: Optional[str] = None
    event_type: Optional[str] = None
    occurred_at: datetime


# ──────────────────────────────────────────────────
# Canonical Event Envelope
# ──────────────────────────────────────────────────

class CanonicalEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    source_system: str
    source_event_id: str
    schema_version: int = 1
    entity_type: EntityType
    occurred_at: datetime
    payload: dict[str, Any]          # Silver — canônico
    raw_payload: dict[str, Any]      # Bronze — bruto original
    ingest_metadata: IngestMetadata = Field(default_factory=IngestMetadata)

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        uuid.UUID(v)
        return v


# ──────────────────────────────────────────────────
# Feature Vector (Redis online store)
# ──────────────────────────────────────────────────

class PlayerFeatures(BaseModel):
    player_id: str
    tenant_id: str
    computed_at: datetime = Field(default_factory=lambda: datetime.now(_UTC))
    feature_version: int = 1

    # Deposits
    deposit_sum_24h: Decimal = Decimal("0")
    deposit_sum_7d: Decimal = Decimal("0")
    deposit_sum_30d: Decimal = Decimal("0")
    deposit_count_24h: int = 0
    deposit_count_7d: int = 0

    # Withdrawals
    withdrawal_sum_24h: Decimal = Decimal("0")
    withdrawal_sum_7d: Decimal = Decimal("0")
    withdrawal_count_24h: int = 0

    # Bets
    bet_stake_sum_24h: Decimal = Decimal("0")
    bet_stake_sum_7d: Decimal = Decimal("0")

    # Derived
    ratio_withdrawal_to_deposit_7d: Decimal = Decimal("0")
    baseline_avg_daily_deposit: Decimal = Decimal("0")
    baseline_stddev_deposit: Decimal = Decimal("0")
    zscore_current_deposit_vs_baseline: Decimal = Decimal("0")

    # Flags
    new_payment_instrument_flag: bool = False
    new_device_flag: bool = False
    shared_device_count: int = 0
    shared_bank_account_count: int = 0

    # Chargeback / reversals
    chargeback_count_30d: int = 0
    failed_deposit_count_24h: int = 0

    def to_redis_dict(self) -> dict[str, str]:
        """Serializa para Redis hash (tudo string)."""
        return {k: str(v) for k, v in self.model_dump().items()}

    @classmethod
    def from_redis_dict(cls, data: dict[str, str]) -> "PlayerFeatures":
        return cls(**data)


# ──────────────────────────────────────────────────
# Alert schema (usado pelo rules_engine e API)
# ──────────────────────────────────────────────────

class AlertSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertType(str, Enum):
    RULE = "RULE"
    ANOMALY = "ANOMALY"
    COMPOSITE = "COMPOSITE"


class AlertEvidence(BaseModel):
    rule_id: Optional[str] = None
    rule_version: int = 1
    triggered_condition: Optional[str] = None
    feature_snapshot: dict[str, Any] = Field(default_factory=dict)
    threshold_values: dict[str, Any] = Field(default_factory=dict)
    anomaly_score: Optional[float] = None
    top_drivers: list[dict[str, Any]] = Field(default_factory=list)
    raw_event_ids: list[str] = Field(default_factory=list)


class AlertMessage(BaseModel):
    """Mensagem publicada no tópico scoring.alerts."""
    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    player_id: str
    player_cpf: Optional[str] = None
    alert_type: AlertType = AlertType.RULE
    severity: AlertSeverity
    title: str
    description: str
    evidence: AlertEvidence
    source_event_id: str
    rule_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(_UTC))
    schema_version: int = 1


# ──────────────────────────────────────────────────
# New enterprise schemas
# ──────────────────────────────────────────────────

class AlertLabel(str, Enum):
    TRUE_POSITIVE = "TRUE_POSITIVE"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    NEED_REVIEW = "NEED_REVIEW"


class IngestErrorOut(BaseModel):
    id: str                              # UUID
    tenant_id: str
    ingest_job_id: Optional[str] = None  # UUID FK
    source_system: str
    entity_type: Optional[str] = None
    raw_payload: Optional[str] = None    # ORM column name
    error_reason: str = ""
    error_detail: Optional[dict] = None  # JSONB
    resolved: bool = False
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IngestErrorResolveIn(BaseModel):
    resolution_note: str


class ApiKeyOut(BaseModel):
    id: str                  # UUID
    tenant_id: str
    name: str
    key_prefix: str          # first 8 chars only
    permissions: list[str]   # ORM field name (column: permissions)
    active: bool             # ORM field name (column: active)
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str
    permissions: list[str] = Field(default_factory=list)
    expires_in_days: Optional[int] = None


class ApiKeyCreateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    key_prefix: str
    permissions: list[str]
    active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    raw_key: str             # shown only once on creation

    model_config = {"from_attributes": True}


class PlayerListOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    list_type: str = "MANUAL"
    entry_count: int = 0
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PlayerListCreate(BaseModel):
    name: str
    description: Optional[str] = None
    list_type: str = "MANUAL"


class PlayerListEntryBulk(BaseModel):
    values: list[str]         # CPFs, device IDs, etc.
    value_type: str = "CPF"


class CompoundRuleOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    logic: Optional[str] = None       # DSL expression or JSON (nullable)
    component_rule_ids: list[str]
    score_weights: dict[str, float] = Field(default_factory=dict)
    min_score_threshold: Optional[float] = None
    is_active: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}


class CompoundRuleCreate(BaseModel):
    name: str
    logic: str = Field("AND", max_length=10)
    component_rule_ids: list[str] = Field(default_factory=list)
    score_weights: dict[str, float] = Field(default_factory=dict)
    min_score_threshold: Optional[float] = None


class RuleMacroOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    expression: str
    description: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleMacroCreate(BaseModel):
    name: str
    expression: str
    description: Optional[str] = None


class ScoringConfigOut(BaseModel):
    id: str
    tenant_id: str
    rule_weight: float = 0.4
    ml_weight: float = 0.4
    network_weight: float = 0.2
    low_threshold: float = 30.0
    medium_threshold: float = 60.0
    high_threshold: float = 80.0
    critical_threshold: float = 95.0
    is_active: bool = True
    sla_low_hours: int = 72
    sla_medium_hours: int = 48
    sla_high_hours: int = 24
    sla_critical_hours: int = 4
    data_retention_days: int = 365 * 5
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ScoringConfigUpdate(BaseModel):
    rule_weight: Optional[float] = None
    ml_weight: Optional[float] = None
    network_weight: Optional[float] = None
    low_threshold: Optional[float] = None
    medium_threshold: Optional[float] = None
    high_threshold: Optional[float] = None
    critical_threshold: Optional[float] = None
    sla_low_hours: Optional[int] = None
    sla_medium_hours: Optional[int] = None
    sla_high_hours: Optional[int] = None
    sla_critical_hours: Optional[int] = None
    data_retention_days: Optional[int] = None


class ScoringPreviewIn(BaseModel):
    rule_weight: Optional[float] = None
    ml_weight: Optional[float] = None
    network_weight: Optional[float] = None
    low_threshold: Optional[float] = None
    medium_threshold: Optional[float] = None
    high_threshold: Optional[float] = None
    critical_threshold: Optional[float] = None


class PreviewBandCount(BaseModel):
    low: int = 0
    medium: int = 0
    high: int = 0
    critical: int = 0


class ScoringPreviewOut(BaseModel):
    current: PreviewBandCount
    proposed: PreviewBandCount
    total_alerts_30d: int = 0


class NotificationOut(BaseModel):
    id: str
    tenant_id: str
    user_id: Optional[str] = None
    type: str
    title: str
    body: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    is_read: bool = False
    created_at: datetime
    read_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NotificationCreate(BaseModel):
    user_id: str
    type: str
    title: str
    body: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class ModelRegistryOut(BaseModel):
    id: str
    tenant_id: str
    model_name: str
    model_type: str
    version: str
    training_rows: Optional[int] = None
    feature_columns: list[str] = []
    metrics: dict = {}
    status: str
    is_challenger: bool
    promoted_by: Optional[str] = None
    promoted_at: Optional[datetime] = None
    trained_by: Optional[str] = None
    trained_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class FeatureSnapshotOut(BaseModel):
    id: str
    tenant_id: str
    player_id: str
    snapshot_date: Optional[date]  # ORM Column(Date) → datetime.date object
    features: dict[str, Any]
    feature_version: int = 1
    created_at: datetime

    model_config = {"from_attributes": True}


class FeatureStoreCurrentOut(BaseModel):
    player_id: str
    source: str = "redis"
    feature_version: int = 1
    computed_at: Optional[str] = None
    features: dict[str, Any]


class FeatureStoreHistoryItemOut(BaseModel):
    id: str
    snapshot_date: Optional[date]
    created_at: datetime
    features: dict[str, Any]
    drift_score: Optional[float] = None
    feature_version: int = 1


class FeatureStoreHistoryOut(BaseModel):
    player_id: str
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    count: int
    items: list[FeatureStoreHistoryItemOut]

    model_config = {"populate_by_name": True}


class FeatureStat(BaseModel):
    mean: float
    std: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    count: int = 0  # not persisted in Redis — defaults to 0 for backward compat


class FeaturePopulationStatsOut(BaseModel):
    computed_at: Optional[str] = None
    features: dict[str, FeatureStat] = Field(default_factory=dict)


class SystemFlagOut(BaseModel):
    key: str                     # composite: "{tenant_id}:{flag_name}"
    value: Any = None            # JSONB — the stored flag value
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SystemFlagUpdate(BaseModel):
    value: Any  # JSONB — new flag value (string, bool, int, or object)


# ──────────────────────────────────────────────────
# User management schemas (admin endpoints)
# ──────────────────────────────────────────────────

class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(..., description="One of: ADMIN, AML_ANALYST, AUDITOR")
    password: str = Field(..., min_length=8)


class UserUpdateIn(BaseModel):
    role: Optional[str] = Field(None, description="New role — cannot be SUPER_ADMIN")
    active: Optional[bool] = None


class InviteIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(..., description="Role for the invited user: ADMIN, AML_ANALYST, AUDITOR")


class ReprocessJobIn(BaseModel):
    reason: str = "manual_reprocess"


class MappingVersionOut(BaseModel):
    id: str
    tenant_id: str
    source_system: str
    entity_type: str
    version_number: int
    is_current: bool
    change_notes: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertLabelIn(BaseModel):
    label: AlertLabel
    label_note: Optional[str] = None


class MonthlyReportIn(BaseModel):
    year: int
    month: int        # 1-12
    include_pdf: bool = True


# ──────────────────────────────────────────────────
# Extended PlayerFeatures with M2 features
# ──────────────────────────────────────────────────

class PlayerFeaturesV2(PlayerFeatures):
    """PlayerFeatures v2 with 11 new enterprise features."""
    feature_version: int = 2

    # M2 new features
    deposit_velocity: Decimal = Decimal("0")          # deposits per hour (24h)
    unique_instruments_7d: int = 0
    night_activity_ratio: Decimal = Decimal("0")      # txns 22h-6h / total
    weekend_activity_ratio: Decimal = Decimal("0")
    avg_odds_bet_7d: Optional[Decimal] = None
    win_loss_ratio_30d: Optional[Decimal] = None
    avg_deposit_to_withdrawal_hours: Optional[Decimal] = None  # avg hours
    multi_currency_flag: bool = False
    chargeback_rate_30d: Decimal = Decimal("0")       # chargebacks / deposits
    bonus_to_real_ratio_30d: Decimal = Decimal("0")   # bonus_credited / deposits
    cashout_ratio_7d: Decimal = Decimal("0")          # withdrawals / deposits

    # Network features (graph)
    cluster_id: Optional[str] = None
    cluster_size: int = 0
    shared_instrument_score: Decimal = Decimal("0")   # 0-1 risk score

    def to_redis_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump().items()}
