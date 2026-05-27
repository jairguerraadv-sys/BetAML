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

from pydantic import BaseModel, Field, field_validator, model_validator

_UTC = timezone.utc

# ──────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────

class EntityType(str, Enum):
    PLAYER = "PLAYER"
    TRANSACTION = "TRANSACTION"
    BET = "BET"
    DEVICE_EVENT = "DEVICE_EVENT"
    # Eventos adicionais exigidos pela Lei 14.790/2023 e Portaria SPA/MF 1.231/2024
    KYC_EVENT = "KYC_EVENT"                          # onboarding, PEP, document, income
    RESPONSIBLE_GAMBLING_EVENT = "RESPONSIBLE_GAMBLING_EVENT"  # autoexclusão SIGAP, limites
    ACCOUNT_STATUS_CHANGE = "ACCOUNT_STATUS_CHANGE"  # bloquear/desbloquear por operador


class KycEventSubtype(str, Enum):
    ONBOARDING_COMPLETE  = "ONBOARDING_COMPLETE"
    DOCUMENT_VERIFIED    = "DOCUMENT_VERIFIED"
    DOCUMENT_REJECTED    = "DOCUMENT_REJECTED"
    PEP_UPDATE           = "PEP_UPDATE"
    INCOME_UPDATE        = "INCOME_UPDATE"
    KYC_REVIEW_REQUESTED = "KYC_REVIEW_REQUESTED"


class ResponsibleGamblingSubtype(str, Enum):
    SELF_EXCLUSION_SIGAP    = "SELF_EXCLUSION_SIGAP"    # autoexclusão nacional (Portaria 1.231/2024)
    SELF_EXCLUSION_OPERATOR = "SELF_EXCLUSION_OPERATOR" # autoexclusão voluntária no operador
    DEPOSIT_LIMIT_SET       = "DEPOSIT_LIMIT_SET"
    DEPOSIT_LIMIT_INCREASED = "DEPOSIT_LIMIT_INCREASED" # aumento requer cooling-off (Lei 14.790)
    DEPOSIT_LIMIT_DECREASED = "DEPOSIT_LIMIT_DECREASED"
    SESSION_LIMIT_SET       = "SESSION_LIMIT_SET"
    COOLING_OFF             = "COOLING_OFF"
    REALITY_CHECK           = "REALITY_CHECK"
    EXCLUSION_LIFTED        = "EXCLUSION_LIFTED"


class AccountStatusChangeSubtype(str, Enum):
    BLOCKED_BY_OPERATOR   = "BLOCKED_BY_OPERATOR"
    UNBLOCKED_BY_OPERATOR = "UNBLOCKED_BY_OPERATOR"
    SUSPENDED             = "SUSPENDED"
    REACTIVATED           = "REACTIVATED"
    CLOSED_BY_PLAYER      = "CLOSED_BY_PLAYER"
    CLOSED_BY_OPERATOR    = "CLOSED_BY_OPERATOR"


class IngestMode(str, Enum):
    INCREMENTAL = "incremental"   # evento em tempo real (padrão)
    BACKFILL    = "backfill"       # importação histórica em massa (novo operador)
    REPROCESS   = "reprocess"      # reprocessamento de job com falha existente


class TransactionType(str, Enum):
    DEPOSIT    = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    REVERSAL   = "REVERSAL"    # estorno Pix / devolução administrativa (era CHARGEBACK)
    BONUS      = "BONUS"
    FREE_BET   = "FREE_BET"    # crédito de bônus sem valor real (Portaria 1.143/2024)
    CASHOUT    = "CASHOUT"     # resgate antecipado de aposta em aberto
    ADJUSTMENT = "ADJUSTMENT"
    CHARGEBACK = "CHARGEBACK"  # DEPRECATED — alias para REVERSAL; mantido para retrocompatibilidade


class TransactionStatus(str, Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REVERSED = "REVERSED"


class PaymentMethod(str, Enum):
    PIX         = "PIX"
    TED         = "TED"
    DEBIT       = "DEBIT"        # débito direto em conta corrente
    CARD_DEBIT  = "CARD_DEBIT"   # cartão de débito (permitido para depósito)
    CARD_CREDIT = "CARD_CREDIT"  # cartão de crédito — PROIBIDO para depósito (art. 5º Portaria 1.143/2024)
    WALLET      = "WALLET"       # carteira de pagamento regulada
    OTHER       = "OTHER"
    CARD        = "CARD"         # DEPRECATED — alias mapeado para CARD_DEBIT na ingestão


class BetChannel(str, Enum):
    WEB = "WEB"
    APP = "APP"
    TERMINAL = "TERMINAL"


class ProductType(str, Enum):
    """Modalidade de jogo — Lei 14.790/2023 art. 3º, I e II."""
    SPORTSBOOK   = "SPORTSBOOK"      # apostas esportivas (quota fixa em eventos reais)
    CASINO_LIVE  = "CASINO_LIVE"     # casino ao vivo (roleta, blackjack, bacará)
    SLOT         = "SLOT"            # caça-níqueis / slot machines
    INSTANT_GAME = "INSTANT_GAME"    # jogos instantâneos (crash, mines, plinko)
    BINGO        = "BINGO"           # bingo online
    RASPADINHA   = "RASPADINHA"      # raspadinha virtual (scratch card)
    VIRTUAL      = "VIRTUAL"         # esportes virtuais / eventos simulados


class GameCategory(str, Enum):
    """Categoria do jogo para modalidades não-esportivas."""
    TABLE  = "TABLE"    # jogos de mesa (blackjack, bacará, poker)
    LIVE   = "LIVE"     # dealer ao vivo
    SLOT   = "SLOT"     # slot machines
    INSTANT = "INSTANT" # jogos instantâneos (crash, mines)
    BINGO  = "BINGO"    # bingo online
    SCRATCH = "SCRATCH" # raspadinha digital
    OTHER  = "OTHER"


class PlayerStatus(str, Enum):
    ACTIVE              = "ACTIVE"           # cadastro ativo e válido
    BLOCKED             = "BLOCKED"          # bloqueio administrativo
    SELF_EXCLUDED       = "SELF_EXCLUDED"    # autoexclusão SIGAP (Portaria 1.231/2024)
    PENDING_KYC         = "PENDING_KYC"      # aguardando verificação de identidade


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
    # GAP-E2: modo de ingestão para que rules_engine e stream_processor possam diferenciar
    # fluxos de tempo-real vs. importação histórica vs. reprocessamento.
    ingest_mode: IngestMode = IngestMode.INCREMENTAL
    # job de backfill que originou este evento (rastreabilidade de proveniência)
    backfill_job_id: Optional[str] = None


# ──────────────────────────────────────────────────
# Canonical payloads (Silver)
# ──────────────────────────────────────────────────

class PlayerPayload(BaseModel):
    external_player_id: str
    cpf: str                         # armazenado criptografado no OLTP
    name: str
    birth_date: Optional[str] = None
    pep_flag: bool = False
    status: PlayerStatus = PlayerStatus.ACTIVE     # ciclo de vida do apostador (Portaria 1.231/2024)
    declared_income_monthly: Optional[Decimal] = None
    profession: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    nationality: Optional[str] = "BR"
    registration_date: Optional[datetime] = None
    self_exclusion_flag: bool = False              # autoexclusão ativa no cadastro SIGAP (Portaria 1.231/2024)
    deposit_limit_daily: Optional[Decimal] = None  # limite de depósito diário declarado no KYC


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
    # ── Multi-modalidade (Lei 14.790/2023 art. 3º) ──
    product_type: ProductType = ProductType.SPORTSBOOK
    game_id: Optional[str] = None            # ID do jogo/mesa/máquina (casino/slot)
    game_name: Optional[str] = None          # nome do jogo (ex: "Lightning Roulette")
    game_provider: Optional[str] = None      # provedor (ex: "Evolution", "Pragmatic Play")
    game_category: Optional[GameCategory] = None  # TABLE, LIVE, SLOT, INSTANT, etc.
    rtp_teorico: Optional[Decimal] = None    # Return-to-Player teórico (0.0000–1.0000)


class DeviceEventPayload(BaseModel):
    player_id: Optional[str] = None
    player_cpf: Optional[str] = None
    device_id: str
    ip: Optional[str] = None
    geo_country: Optional[str] = None
    user_agent: Optional[str] = None
    event_type: Optional[str] = None
    occurred_at: datetime


class KycEventPayload(BaseModel):
    """Payload canônico para EntityType.KYC_EVENT (Lei 14.790/2023 art. 20)."""
    player_id: str
    player_cpf: Optional[str] = None
    subtype: KycEventSubtype
    provider: Optional[str] = None           # ex: "serpro", "denatran", "manual"
    document_type: Optional[str] = None      # RG, CNH, PASSAPORTE
    pep_flag: Optional[bool] = None          # se PEP_UPDATE, novo valor
    income_declared: Optional[Decimal] = None  # se INCOME_UPDATE, novo valor
    notes: Optional[str] = None
    occurred_at: datetime


class ResponsibleGamblingEventPayload(BaseModel):
    """Payload canônico para EntityType.RESPONSIBLE_GAMBLING_EVENT.

    Portaria SPA/MF 1.231/2024 — autoexclusão SIGAP e limites voluntários.
    """
    player_id: str
    player_cpf: Optional[str] = None
    subtype: ResponsibleGamblingSubtype
    # Autoexclusão
    exclusion_source: Optional[str] = None   # "SIGAP" | "BETAML" | "OPERATOR"
    exclusion_scope: Optional[str] = None    # "NATIONAL" | "OPERATOR"
    exclusion_duration_days: Optional[int] = None  # None = indefinido
    # Limites de depósito
    old_limit_daily: Optional[Decimal] = None
    new_limit_daily: Optional[Decimal] = None
    effective_at: Optional[datetime] = None  # para DEPOSIT_LIMIT_INCREASED: precisa cumprir cooling-off
    occurred_at: datetime


class AccountStatusChangePayload(BaseModel):
    """Payload canônico para EntityType.ACCOUNT_STATUS_CHANGE."""
    player_id: str
    player_cpf: Optional[str] = None
    subtype: AccountStatusChangeSubtype
    reason: Optional[str] = None
    operator_user_id: Optional[str] = None  # quem executou a ação
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
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

    @model_validator(mode="after")
    def validate_payload_shape(self) -> "CanonicalEvent":
        """Verifica que payload contém os campos mínimos esperados para o entity_type."""
        _REQUIRED: dict[str, set[str]] = {
            "PLAYER":                      {"external_player_id", "cpf"},
            "TRANSACTION":                 {"amount", "occurred_at"},
            "BET":                         {"stake_amount", "placed_at"},
            "DEVICE_EVENT":                {"device_id", "occurred_at"},
            "KYC_EVENT":                   {"player_id", "subtype", "occurred_at"},
            "RESPONSIBLE_GAMBLING_EVENT":  {"player_id", "subtype", "occurred_at"},
            "ACCOUNT_STATUS_CHANGE":       {"player_id", "subtype", "occurred_at"},
        }
        required = _REQUIRED.get(self.entity_type.value if hasattr(self.entity_type, 'value') else str(self.entity_type), set())
        missing = required - set(self.payload.keys())
        if missing:
            raise ValueError(
                f"payload para entity_type={self.entity_type!r} está faltando campos obrigatórios: {sorted(missing)}"
            )
        return self


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
        return cls.model_validate(data)


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
    source_system: Optional[str] = None
    permissions: list[str]   # ORM field name (column: permissions)
    active: bool             # ORM field name (column: active)
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str
    source_system: Optional[str] = None
    permissions: list[str] = Field(default_factory=lambda: ["ingest"])
    expires_in_days: Optional[int] = None


class ApiKeyCreateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    key_prefix: str
    source_system: Optional[str] = None
    permissions: list[str]
    active: bool
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    raw_key: str             # shown only once on creation

    model_config = {"from_attributes": True}


class ApiKeyUsageOut(BaseModel):
    key_id: str
    key_prefix: str
    name: str
    source_system: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)
    active: bool = True
    last_used_at: Optional[datetime] = None
    total_requests_30d: int = 0
    days: dict[str, int] = Field(default_factory=dict)


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
    sla_low_hours: int = 168
    sla_medium_hours: int = 72
    sla_high_hours: int = 24
    sla_critical_hours: int = 4
    data_retention_days: int = 365 * 5
    data_retention_raw_years: int = 5
    data_retention_silver_years: int = 5
    data_retention_gold_years: int = 3
    auto_case_threshold: float = 0.75
    risk_band_low_threshold: float = 0.35
    risk_band_high_threshold: float = 0.70
    income_volume_ratio_threshold: float = 1.50
    ingest_rate_limit_tpm: int = 1000
    ml_challenger_pct: int = 0
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
    data_retention_raw_years: Optional[int] = None
    data_retention_silver_years: Optional[int] = None
    data_retention_gold_years: Optional[int] = None
    auto_case_threshold: Optional[float] = None
    risk_band_low_threshold: Optional[float] = None
    risk_band_high_threshold: Optional[float] = None
    income_volume_ratio_threshold: Optional[float] = None
    ingest_rate_limit_tpm: Optional[int] = None
    ml_challenger_pct: Optional[int] = None


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
    model_name: Optional[str] = None
    model_type: str
    algorithm: Optional[str] = None
    version: str
    dataset_window_start: Optional[datetime] = None
    dataset_window_end: Optional[datetime] = None
    dataset_window_days: Optional[int] = None
    sample_count: Optional[int] = None
    training_rows: Optional[int] = None
    feature_columns: list[str] = []
    metrics: dict = {}
    trained_on_synthetic: bool = False
    artifact_path: Optional[str] = None
    artifact_uri: Optional[str] = None
    status: str
    is_challenger: bool
    promoted_by: Optional[str] = None
    promoted_at: Optional[datetime] = None
    trained_by: Optional[str] = None
    trained_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class ModelABTimelinePointOut(BaseModel):
    date: str
    champion_inferences: int = 0
    challenger_inferences: int = 0
    champion_avg_score: Optional[float] = None
    challenger_avg_score: Optional[float] = None
    champion_tp: int = 0
    champion_fp: int = 0
    challenger_tp: int = 0
    challenger_fp: int = 0

    model_config = {"protected_namespaces": ()}


class ModelABMetricsOut(BaseModel):
    model_id: str
    model_name: Optional[str] = None
    role: str = "champion"
    status: str
    days_window: int = 30
    champion_model_id: Optional[str] = None
    challenger_model_id: Optional[str] = None
    champion_inferences: int = 0
    challenger_inferences: int = 0
    champion_avg_score: Optional[float] = None
    challenger_avg_score: Optional[float] = None
    champion_precision_estimated: Optional[float] = None
    challenger_precision_estimated: Optional[float] = None
    champion_recall_estimated: Optional[float] = None
    challenger_recall_estimated: Optional[float] = None
    champion_false_positive_rate: Optional[float] = None
    challenger_false_positive_rate: Optional[float] = None
    timeline: list[ModelABTimelinePointOut] = Field(default_factory=list)

    model_config = {"protected_namespaces": ()}


class ModelPerformanceTotalsOut(BaseModel):
    total_alerts: int = 0
    labeled_alerts: int = 0
    true_positive_count: int = 0
    false_positive_count: int = 0
    unknown_count: int = 0
    precision_estimated: float = 0.0
    false_positive_rate: float = 0.0
    recall_estimated: float = 0.0


class ModelPerformancePointOut(BaseModel):
    date: str
    total_alerts: int = 0
    true_positive_count: int = 0
    false_positive_count: int = 0
    unknown_count: int = 0


class RulePerformanceItemOut(BaseModel):
    rule_id: Optional[str] = None
    rule_name: str
    total_alerts: int = 0
    true_positive_count: int = 0
    false_positive_count: int = 0
    unknown_count: int = 0
    precision_estimated: float = 0.0
    false_positive_rate: float = 0.0


class ModelPerformanceItemOut(BaseModel):
    model_id: str
    model_name: Optional[str] = None
    algorithm: Optional[str] = None
    status: str
    total_alerts: int = 0
    true_positive_count: int = 0
    false_positive_count: int = 0
    unknown_count: int = 0
    precision_estimated: float = 0.0
    recall_estimated: float = 0.0
    false_positive_rate: float = 0.0

    model_config = {"protected_namespaces": ()}


class ModelPerformanceSummaryOut(BaseModel):
    days_window: int = 30
    challenger_split_pct: int = 0
    totals: ModelPerformanceTotalsOut = Field(default_factory=ModelPerformanceTotalsOut)
    by_day: list[ModelPerformancePointOut] = Field(default_factory=list)
    by_rule: list[RulePerformanceItemOut] = Field(default_factory=list)
    by_model: list[ModelPerformanceItemOut] = Field(default_factory=list)


class AlertExplainabilityFeatureOut(BaseModel):
    feature: str
    current_value: Any = None
    baseline_value: Optional[float] = None
    delta: Optional[float] = None
    contribution: float = 0.0


class AlertExplainabilityOut(BaseModel):
    alert_id: str
    model_id: Optional[str] = None
    explanation_method: str = "heuristic_proxy"
    anomaly_score: float = 0.0
    top_features: list[AlertExplainabilityFeatureOut] = Field(default_factory=list)

    model_config = {"protected_namespaces": ()}


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
    snapshot_version: int = 1
    entity_type: str = "PLAYER"
    snapshot_date: Optional[str] = None
    gold_object_path: Optional[str] = None
    computed_at: Optional[str] = None
    features: dict[str, Any]


class FeatureStoreHistoryItemOut(BaseModel):
    id: str
    snapshot_date: Optional[date]
    created_at: datetime
    features: dict[str, Any]
    drift_score: Optional[float] = None
    feature_version: int = 1
    entity_type: str = "PLAYER"
    gold_object_path: Optional[str] = None


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


class FeatureQualityFindingOut(BaseModel):
    feature_name: str
    finding_type: str
    current_value: float
    previous_value: Optional[float] = None
    delta: Optional[float] = None
    severity: str = "WARN"


class FeatureQualityStatusOut(BaseModel):
    feature_date: Optional[str] = None
    previous_feature_date: Optional[str] = None
    drift_detected: bool = False
    max_drift_score: float = 0.0
    admin_notification_sent: bool = False
    findings: list[FeatureQualityFindingOut] = Field(default_factory=list)


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
    roles: list[str] | None = None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(..., description="One of: Operador_Analista, Operador_Gestor, Operador_AdminTecnico")
    password: str = Field(..., min_length=8)


class UserUpdateIn(BaseModel):
    role: Optional[str] = Field(None, description="New role — cannot be SUPER_ADMIN")
    active: Optional[bool] = None


class InviteIn(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    role: str = Field(..., description="Role for the invited user")


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
    inconsistent_currency_flag: bool = False       # transação em moeda diferente de BRL — anomalia de dado (era multi_currency_flag)
    chargeback_rate_30d: Decimal = Decimal("0")     # taxa de estornos/reversões nos últimos 30d
    bonus_to_real_ratio_30d: Decimal = Decimal("0") # proporção bônus/depósito real (indicador de abuso de bônus)
    cashout_ratio_7d: Decimal = Decimal("0")         # proporção saques/depósitos em 7d (indicador de round-trip)

    # Network features (graph)
    cluster_id: Optional[str] = None
    cluster_size: int = 0
    shared_instrument_score: Decimal = Decimal("0")   # 0-1 risk score

    def to_redis_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump().items()}


# ──────────────────────────────────────────────────
# Module 5 — Case Workflow + Player Enrichment
# ──────────────────────────────────────────────────

class CaseCommentIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    mentions: list[str] = Field(default_factory=list)


class CaseLinkAlertIn(BaseModel):
    alert_id: str


class TransactionChartItem(BaseModel):
    day: str            # ISO date YYYY-MM-DD
    deposit_sum: float
    withdrawal_sum: float


class BetChartItem(BaseModel):
    day: str
    stake_sum: float


class PaymentInstrumentSummary(BaseModel):
    payment_instrument: Optional[str] = None
    payment_method: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    tx_count: int = 0


class PlayerNetworkItem(BaseModel):
    player_id: str
    shared_by: list[dict[str, str]] = Field(default_factory=list)
