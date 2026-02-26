"""Canonical Pydantic v2 models for the BetAML ingestion pipeline."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TransactionType(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    CHARGEBACK = "CHARGEBACK"
    BONUS = "BONUS"
    ADJUSTMENT = "ADJUSTMENT"


class PaymentMethod(str, Enum):
    PIX = "PIX"
    TED = "TED"
    CARD = "CARD"
    WALLET = "WALLET"
    OTHER = "OTHER"


class TransactionStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class Channel(str, Enum):
    WEB = "WEB"
    APP = "APP"
    TERMINAL = "TERMINAL"


class EntityType(str, Enum):
    PLAYER = "PLAYER"
    TRANSACTION = "TRANSACTION"
    BET = "BET"
    DEVICE_EVENT = "DEVICE_EVENT"


# ---------------------------------------------------------------------------
# Ingest metadata
# ---------------------------------------------------------------------------


class IngestMetadata(BaseModel):
    receivedAt: datetime
    fileName: str
    apiKeyId: str
    checksum: str
    mapperVersion: str


# ---------------------------------------------------------------------------
# Entity payloads
# ---------------------------------------------------------------------------


class CanonicalPlayerPayload(BaseModel):
    externalPlayerId: str
    cpf: str
    name: str
    birthDate: datetime
    pepFlag: bool
    declaredIncomeMonthly: Decimal
    profession: str

    @field_validator("declaredIncomeMonthly", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class CanonicalTransactionPayload(BaseModel):
    externalTransactionId: str
    playerCpf: str
    playerId: str
    type: TransactionType
    amount: Decimal
    currency: str = "BRL"
    method: PaymentMethod
    status: TransactionStatus
    paymentInstrument: dict[str, Any]
    occurredAt: datetime

    @field_validator("amount", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class CanonicalBetPayload(BaseModel):
    externalBetId: str
    playerCpf: str
    playerId: str
    stakeAmount: Decimal
    odds: Decimal
    potentialPayout: Decimal
    settledPayout: Optional[Decimal] = None
    marketType: str
    sport: str
    eventId: str
    selection: str
    channel: Channel
    placedAt: datetime
    settledAt: Optional[datetime] = None

    @field_validator("stakeAmount", "odds", "potentialPayout", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    @field_validator("settledPayout", mode="before")
    @classmethod
    def coerce_optional_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return Decimal(str(v))


class CanonicalDeviceEventPayload(BaseModel):
    deviceId: str
    ip: str
    geoCountry: str
    userAgent: str
    occurredAt: datetime


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class CanonicalEventEnvelope(BaseModel):
    eventId: UUID = Field(default_factory=uuid4)
    tenantId: UUID
    sourceSystem: str
    sourceEventId: str
    schemaVersion: int = 1
    entityType: EntityType
    occurredAt: datetime
    payload: dict[str, Any]
    rawPayload: dict[str, Any]
    ingestMetadata: IngestMetadata
