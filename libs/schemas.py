"""
Canonical Event Envelope + payload schemas (Pydantic v2).
Shared by API, stream_processor, rules_engine, ml_service.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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
    received_at: datetime = Field(default_factory=datetime.utcnow)
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
    computed_at: datetime = Field(default_factory=datetime.utcnow)
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: int = 1
