"""
Mapping Engine — converte dados brutos de backoffices para o modelo canônico.

Suporta transforms:
  - parseDate       : converte string de data para ISO8601
  - normalizeCpf    : remove pontuação, valida dígitos
  - mapEnum         : mapeia valor para enum canônico
  - coerceDecimal   : string/int → Decimal
  - coerceArray     : string CSV ou já lista → list[str]
  - lowercase       : string para lowercase
  - strip           : remove espaços
  - constant        : valor fixo
  - copy / copyField: copia de outro campo
  - conditional     : mapeamento condicional (if/else via dict lookup)
  - normalize       : alias mapEnum (normaliza texto bruto para enum canônico)
  - extractXMLAttr  : extrai atributo específico de elemento XML já parseado
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

# Literal type enumerating all supported transform names
TransformType = Literal[
    "copy",
    "copyField",
    "parseDate",
    "normalizeCpf",
    "coerceDecimal",
    "coerceArray",
    "lowercase",
    "uppercase",
    "strip",
    "constant",
    "mapEnum",
    "normalize",
    "conditional",
    "extractXMLAttr",
    "stringify",
]


# ──────────────────────────────────────────────────
# MappingConfig schema (armazenado no Postgres como JSON)
# ──────────────────────────────────────────────────

class TransformRule(BaseModel):
    target: str                      # campo canônico destino
    source: str | None = None        # campo fonte (dot-notation)
    transform: str = "copy"          # tipo de transform
    params: dict[str, Any] = {}      # parâmetros do transform
    required: bool = False


class MappingConfigSchema(BaseModel):
    version: str = "1.0"
    source_system: str
    entity_type: str                 # PLAYER / TRANSACTION / BET / DEVICE_EVENT
    fields: list[TransformRule]


# ──────────────────────────────────────────────────
# Pre-defined connectors (BackofficeAlpha / BackofficeBeta)
# ──────────────────────────────────────────────────

BACKOFFICE_ALPHA_TRANSACTION: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeAlpha",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "transaction_id",  "source": "transactionId",  "transform": "copy"},
        {"target": "player_id",       "source": "playerId",       "transform": "copy"},
        {"target": "type",            "source": "type",           "transform": "copy"},
        {"target": "amount",          "source": "amount",         "transform": "coerceDecimal"},
        {"target": "currency",        "source": "currency",       "transform": "copy"},
        {"target": "method",          "source": "paymentMethod",  "transform": "copy"},
        {"target": "status",          "source": "status",         "transform": "copy"},
        {"target": "occurred_at",     "source": "transactionDate","transform": "parseDate"},
    ],
}

BACKOFFICE_ALPHA_PLAYER: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeAlpha",
    "entity_type": "PLAYER",
    "fields": [
        {"target": "external_player_id",       "source": "playerId",       "transform": "copy"},
        {"target": "full_name",                "source": "fullName",       "transform": "strip"},
        {"target": "cpf",                      "source": "cpf",            "transform": "normalizeCpf"},
        {"target": "email",                    "source": "email",          "transform": "lowercase"},
        {"target": "birth_date",               "source": "dateOfBirth",    "transform": "parseDate"},
        {"target": "pep_flag",                 "source": "pepFlag",        "transform": "copy"},
        {"target": "declared_income_monthly",  "source": "declaredIncome", "transform": "coerceDecimal"},
    ],
}

BACKOFFICE_BETA_TRANSACTION: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeBeta",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "transaction_id",  "source": "txn_id",      "transform": "copy"},
        {"target": "player_id",       "source": "user_id",     "transform": "copy"},
        {"target": "type",            "source": "txn_type",    "transform": "copy"},
        {"target": "amount",          "source": "value",       "transform": "coerceDecimal"},
        {"target": "currency",        "source": "ccy",         "transform": "copy"},
        {"target": "method",          "source": "method",      "transform": "copy"},
        {"target": "status",          "source": "txn_status",  "transform": "copy"},
        {"target": "occurred_at",     "source": "occurred_utc","transform": "parseDate"},
    ],
}

BACKOFFICE_BETA_PLAYER: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeBeta",
    "entity_type": "PLAYER",
    "fields": [
        {"target": "external_player_id", "source": "id",        "transform": "copy"},
        {"target": "cpf",                "source": "cpf",       "transform": "normalizeCpf"},
        {"target": "name",               "source": "nome",      "transform": "strip"},
        {"target": "birth_date",         "source": "nascimento","transform": "parseDate"},
        {"target": "pep_flag",           "source": "pep",       "transform": "copy"},
    ],
}

# ── BackofficeX — CSV diário (depositos / cadastros / apostas) ────────────────
# Espelha docs/mapping-config-example.yaml — use como template de onboarding.

BACKOFFICE_X_TRANSACTION: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeX",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "id_transacao",       "transform": "copy",          "required": True},
        {"target": "player_cpf",              "source": "cpf_apostador",       "transform": "normalizeCpf",  "required": True},
        {"target": "external_player_id",      "source": "id_apostador",        "transform": "copy"},
        {"target": "type",                    "source": "tipo_mov",             "transform": "mapEnum",       "required": True,
         "params": {"DEP": "DEPOSIT", "SAQ": "WITHDRAWAL", "SAQ_INT": "WITHDRAWAL",
                    "CB": "REVERSAL", "BON": "BONUS", "BON_BEM": "BONUS",
                    "AJU": "ADJUSTMENT", "APO": "FREE_BET", "CSH": "CASHOUT", "_default": "ADJUSTMENT"}},
        {"target": "amount",                  "source": "vlr_bruto",           "transform": "coerceDecimal", "required": True},
        {"target": "currency",                "source": "moeda",               "transform": "copy"},
        {"target": "method",                  "source": "metodo_pgto",         "transform": "mapEnum",       "required": True,
         "params": {"PIX": "PIX", "pix": "PIX", "TED": "TED", "ted": "TED",
                    "DOC": "TED", "CARTAO": "CARD_DEBIT", "CARD": "CARD_DEBIT",
                    "credit_card": "CARD_CREDIT", "debit_card": "CARD_DEBIT",
                    "WALLET": "WALLET", "_default": "OTHER"}},
        {"target": "status",                  "source": "status_transacao",    "transform": "mapEnum",       "required": True,
         "params": {"LIQUIDADA": "SETTLED", "CONCLUIDA": "SETTLED",
                    "EM_PROC": "PENDING",  "PENDENTE": "PENDING",
                    "FALHA": "FAILED",     "ERRO": "FAILED",
                    "CANCELADA": "REVERSED", "ESTORNADA": "REVERSED",
                    "_default": "PENDING"}},
        {"target": "occurred_at",             "source": "dt_transacao",        "transform": "parseDate",     "required": True},
        {"target": "description",             "source": "descricao",           "transform": "strip"},
        {"target": "source_system",           "transform": "constant",         "params": {"value": "BackofficeX"}},
    ],
}

BACKOFFICE_X_PLAYER: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeX",
    "entity_type": "PLAYER",
    "fields": [
        {"target": "external_player_id",       "source": "id_cliente",       "transform": "copy",         "required": True},
        {"target": "name",                     "source": "nome_completo",    "transform": "strip",        "required": True},
        {"target": "cpf",                      "source": "cpf",              "transform": "normalizeCpf", "required": True},
        {"target": "birth_date",               "source": "dt_nasc",          "transform": "parseDate"},
        {"target": "email",                    "source": "email",            "transform": "lowercase"},
        {"target": "phone",                    "source": "telefone",         "transform": "strip"},
        {"target": "profession",               "source": "ocupacao",         "transform": "strip"},
        {"target": "declared_income_monthly",  "source": "renda_mensal",     "transform": "coerceDecimal"},
        {"target": "pep_flag",                 "source": "pep",              "transform": "mapEnum",
         "params": {"true": True, "false": False, "True": True, "False": False,
                    "S": True, "N": False, "SIM": True, "NAO": False, "NÃO": False,
                    "_default": False}},
        {"target": "registration_date",        "source": "dt_cadastro",      "transform": "parseDate"},
        {"target": "nationality",              "source": "nacionalidade",    "transform": "copy"},
        {"target": "source_system",            "transform": "constant",      "params": {"value": "BackofficeX"}},
    ],
}

BACKOFFICE_X_BET: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeX",
    "entity_type": "BET",
    "fields": [
        {"target": "external_bet_id",    "source": "id_aposta",       "transform": "copy",         "required": True},
        {"target": "player_cpf",         "source": "cpf_apostador",   "transform": "normalizeCpf", "required": True},
        {"target": "external_player_id", "source": "id_apostador",    "transform": "copy"},
        {"target": "stake_amount",       "source": "vlr_aposta",      "transform": "coerceDecimal","required": True},
        {"target": "odds",               "source": "odds",            "transform": "coerceDecimal"},
        {"target": "potential_payout",   "source": "retorno_potencial","transform": "coerceDecimal"},
        {"target": "settled_payout",     "source": "retorno_efetivo", "transform": "coerceDecimal"},
        {"target": "market_type",        "source": "tipo_mercado",    "transform": "normalize",
         "params": {"resultado_final": "match_result", "handicap": "handicap",
                    "mais_menos": "over_under", "ambas_marcam": "both_to_score",
                    "placar_exato": "correct_score", "total_gols": "over_under",
                    "_default": "other"}},
        {"target": "sport",              "source": "esporte",         "transform": "normalize",
         "params": {"futebol": "football", "basquete": "basketball", "tenis": "tennis",
                    "volei": "volleyball", "esports": "esports", "_default": "other"}},
        {"target": "event_id",           "source": "id_evento",       "transform": "copy"},
        {"target": "selection",          "source": "selecao",         "transform": "strip"},
        {"target": "channel",            "source": "canal",           "transform": "mapEnum",
         "params": {"APP": "APP", "WEB": "WEB", "SITE": "WEB",
                    "TERMINAL": "TERMINAL", "PDV": "TERMINAL", "_default": "WEB"}},
        {"target": "placed_at",          "source": "dt_aposta",       "transform": "parseDate",    "required": True},
        {"target": "settled_at",         "source": "dt_liquidacao",   "transform": "parseDate"},
        {"target": "status",             "source": "status_aposta",   "transform": "mapEnum",
         "params": {"GANHA": "WON", "PERDIDA": "LOST", "CANCELADA": "CANCELLED",
                    "PENDENTE": "PENDING", "OPEN": "PENDING", "VOID": "VOIDED",
                    "_default": "PENDING"}},
        {"target": "cashout_amount",     "source": "vlr_cashout",     "transform": "coerceDecimal"},
        {"target": "product_type",        "source": "tipo_produto",    "transform": "mapEnum",
         "params": {"ESPORTIVO": "SPORTSBOOK", "SPORTSBOOK": "SPORTSBOOK",
                    "CASINO_AO_VIVO": "CASINO_LIVE", "CASINO_LIVE": "CASINO_LIVE",
                    "CACA_NIQUEL": "SLOT", "SLOT": "SLOT", "SLOTS": "SLOT",
                    "JOGO_INSTANTANEO": "INSTANT_GAME", "INSTANT_GAME": "INSTANT_GAME",
                    "BINGO": "BINGO", "RASPADINHA": "RASPADINHA",
                    "VIRTUAL": "VIRTUAL", "_default": "SPORTSBOOK"}},
        {"target": "game_id",             "source": "id_jogo",          "transform": "copy"},
        {"target": "game_name",           "source": "nome_jogo",        "transform": "strip"},
        {"target": "game_provider",       "source": "provedor_jogo",    "transform": "strip"},
        {"target": "game_category",       "source": "categoria_jogo",   "transform": "mapEnum",
         "params": {"MESA": "TABLE", "TABLE": "TABLE",
                    "AO_VIVO": "LIVE", "LIVE": "LIVE",
                    "SLOT": "SLOT", "CACA_NIQUEL": "SLOT",
                    "INSTANTANEO": "INSTANT", "INSTANT": "INSTANT",
                    "BINGO": "BINGO", "RASPADINHA": "SCRATCH",
                    "_default": "OTHER"}},
        {"target": "rtp_teorico",         "source": "rtp",              "transform": "coerceDecimal"},
        {"target": "source_system",      "transform": "constant",     "params": {"value": "BackofficeX"}},
    ],
}

BACKOFFICE_X_DEVICE_EVENT: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeX",
    "entity_type": "DEVICE_EVENT",
    "fields": [
        {"target": "external_player_id", "source": "id_apostador",    "transform": "copy"},
        {"target": "player_cpf",         "source": "cpf_apostador",   "transform": "normalizeCpf"},
        {"target": "device_id",          "source": "id_dispositivo",  "transform": "copy",         "required": True},
        {"target": "ip",                 "source": "ip_acesso",       "transform": "strip"},
        {"target": "geo_country",        "source": "pais",            "transform": "uppercase",
         "params": {}},
        {"target": "user_agent",         "source": "navegador",       "transform": "strip"},
        {"target": "event_type",         "source": "tipo_evento",     "transform": "mapEnum",
         "params": {"LOGIN": "LOGIN", "LOGOUT": "LOGOUT", "APOSTA": "BET",
                    "SAQUE": "WITHDRAWAL", "DEPOSITO": "DEPOSIT",
                    "_default": "OTHER"}},
        {"target": "occurred_at",        "source": "dt_evento",       "transform": "parseDate",    "required": True},
        {"target": "source_system",      "transform": "constant",     "params": {"value": "BackofficeX"}},
    ],
}

BACKOFFICE_CONFIGS: dict[str, dict[str, Any]] = {
    "BackofficeAlpha:TRANSACTION": BACKOFFICE_ALPHA_TRANSACTION,
    "BackofficeAlpha:PLAYER":      BACKOFFICE_ALPHA_PLAYER,
    "BackofficeBeta:TRANSACTION":  BACKOFFICE_BETA_TRANSACTION,
    "BackofficeBeta:PLAYER":       BACKOFFICE_BETA_PLAYER,
    # BackofficeX — template de onboarding (ver docs/mapping-config-example.yaml)
    "BackofficeX:TRANSACTION":     BACKOFFICE_X_TRANSACTION,
    "BackofficeX:PLAYER":          BACKOFFICE_X_PLAYER,
    "BackofficeX:BET":             BACKOFFICE_X_BET,
    "BackofficeX:DEVICE_EVENT":    BACKOFFICE_X_DEVICE_EVENT,
}


# ──────────────────────────────────────────────────
# Transform functions
# ──────────────────────────────────────────────────

def _get_nested(source: dict[str, Any], path: str) -> Any:
    """Suporta path com ponto: 'player.cpf'."""
    parts = path.split(".")
    obj: Any = source
    for p in parts:
        if isinstance(obj, dict):
            obj = obj.get(p)
        else:
            obj = getattr(obj, p, None)
        if obj is None:
            return None
    return obj


DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M:%S",
]


def _parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return s  # devolve como está se não reconhecer


def _normalize_cpf(value: Any) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits  # 11 dígitos sem formatação


def _coerce_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None


TRANSFORMS: dict[str, Any] = {
    "copy":          lambda v, p: v,
    "parseDate":     lambda v, p: _parse_date(v),
    "normalizeCpf":  lambda v, p: _normalize_cpf(v),
    "coerceDecimal": lambda v, p: (lambda d: str(d) if d is not None else None)(_coerce_decimal(v)),
    "coerceArray":   lambda v, p: (v if isinstance(v, list) else [x.strip() for x in str(v).split(",")]) if v is not None else [],
    "lowercase":     lambda v, p: str(v).lower() if v is not None else None,
    "uppercase":     lambda v, p: str(v).upper() if v is not None else None,
    "strip":         lambda v, p: str(v).strip() if v is not None else None,
    "constant":      lambda v, p: p.get("value"),
    "copyField":     lambda v, p: v,
    "mapEnum":       lambda v, p: p.get(str(v), p.get("_default", str(v) if v is not None else None)),
    "normalize":     lambda v, p: p.get(str(v).lower(), p.get("_default", str(v).lower() if v is not None else None)),
    "conditional":   lambda v, p: p.get(str(v), p.get("_default", None)),
    "extractXMLAttr": lambda v, p: (v or {}).get(p.get("attr", "")) if isinstance(v, dict) else None,
}


# ──────────────────────────────────────────────────
# MappingEngine
# ──────────────────────────────────────────────────

class MappingError(Exception):
    pass


class MappingEngine:
    """
    Aplica uma MappingConfig a um dict de entrada e retorna o dict canônico.

    Uso:
        engine = MappingEngine(config_dict)
        canonical = engine.apply(raw_row)
    """

    def __init__(self, config: dict[str, Any]):
        self._config = MappingConfigSchema(**config)

    @property
    def source_system(self) -> str:
        return self._config.source_system

    @property
    def entity_type(self) -> str:
        return self._config.entity_type

    def apply(self, raw: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in self._config.fields:
            # Resolve valor fonte
            if field.transform == "constant":
                raw_value = None
            elif field.source:
                raw_value = _get_nested(raw, field.source)
            else:
                raw_value = None

            # Aplica transform
            fn = TRANSFORMS.get(field.transform)
            if fn is None:
                raise MappingError(f"Transform desconhecido: {field.transform!r}")

            value = fn(raw_value, field.params)

            # Checar campo obrigatório
            if field.required and value is None:
                raise MappingError(
                    f"Campo obrigatório '{field.target}' é None após transform "
                    f"'{field.transform}' (source='{field.source}', raw='{raw_value}')"
                )

            result[field.target] = value
        return result


def get_default_mapping(source_system: str, entity_type: str) -> dict[str, Any] | None:
    """Retorna config padrão pré-definida ou None."""
    normalized_entity_type = entity_type.strip().upper()
    return BACKOFFICE_CONFIGS.get(f"{source_system}:{normalized_entity_type}")


CANONICAL_INGEST_SCHEMA: dict[str, dict[str, Any]] = {
    "TRANSACTION": {
        "required_any": [
            {"external_transaction_id", "transaction_id", "event_id"},
            {"player_id", "player_cpf", "external_player_id"},
            {"type", "transaction_type"},
            {"amount"},
            {"occurred_at"},
        ],
        "allowed_fields": {
            "entity_type",
            "event_id",
            "external_transaction_id",
            "transaction_id",
            "player_id",
            "player_cpf",
            "external_player_id",
            "type",
            "transaction_type",
            "amount",
            "currency",
            "method",
            "status",
            "occurred_at",
            "description",
            "device_id",
            "instrument_type",
            "instrument_token",
            "payment_instrument",
            "ip_address",
            "session_id",
            "metadata",
            "source_system",
        },
    },
    "BET": {
        "required_any": [
            {"external_bet_id", "bet_id", "event_id"},
            {"player_id", "player_cpf", "external_player_id"},
            {"stake_amount", "amount"},
            {"placed_at", "occurred_at"},
        ],
        "allowed_fields": {
            "entity_type",
            "event_id",
            "external_bet_id",
            "bet_id",
            "player_id",
            "player_cpf",
            "external_player_id",
            "stake_amount",
            "amount",
            "odds",
            "potential_payout",
            "settled_payout",
            "market_type",
            "sport",
            "event_id_ref",
            "event_id",
            "selection",
            "channel",
            "placed_at",
            "occurred_at",
            "settled_at",
            "status",
            "cashout_amount",
            "outcome",
            "device_id",
            "metadata",
            "source_system",
        },
    },
    "PLAYER": {
        "required_any": [
            {"external_player_id", "player_id"},
            {"cpf"},
            {"name", "full_name"},
        ],
        "allowed_fields": {
            "entity_type",
            "player_id",
            "external_player_id",
            "cpf",
            "name",
            "full_name",
            "birth_date",
            "pep_flag",
            "declared_income_monthly",
            "profession",
            "email",
            "phone",
            "nationality",
            "registration_date",
            "registered_since",
            "source_system",
            "metadata",
        },
    },
    "DEVICE_EVENT": {
        "required_any": [
            {"device_id"},
            {"occurred_at"},
        ],
        "allowed_fields": {
            "entity_type",
            "player_id",
            "player_cpf",
            "device_id",
            "ip",
            "ip_address",
            "geo_country",
            "country_code",
            "user_agent",
            "event_type",
            "action",
            "occurred_at",
            "source_system",
            "metadata",
        },
    },
}


def validate_mapping_targets_against_canonical_schema(config: dict[str, Any]) -> dict[str, Any]:
    normalized = MappingConfigSchema(**config)
    entity_type = normalized.entity_type.strip().upper()
    schema = CANONICAL_INGEST_SCHEMA.get(entity_type)
    if schema is None:
        return {
            "entity_type": entity_type,
            "valid": False,
            "missing_required_groups": [f"entity_type '{entity_type}' não suportado"],
            "unknown_targets": [],
            "allowed_fields": [],
        }

    targets = [field.target for field in normalized.fields]
    target_set = set(targets)
    allowed_fields = sorted(schema["allowed_fields"])
    unknown_targets = sorted(target_set - set(allowed_fields))
    missing_required_groups = [
        sorted(group)
        for group in schema["required_any"]
        if not (target_set & set(group))
    ]
    return {
        "entity_type": entity_type,
        "valid": not unknown_targets and not missing_required_groups,
        "missing_required_groups": missing_required_groups,
        "unknown_targets": unknown_targets,
        "allowed_fields": allowed_fields,
    }


def validate_mapped_payload_against_canonical_schema(entity_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_entity = entity_type.strip().upper()
    schema = CANONICAL_INGEST_SCHEMA.get(normalized_entity)
    if schema is None:
        return {
            "entity_type": normalized_entity,
            "valid": False,
            "missing_required_groups": [f"entity_type '{normalized_entity}' não suportado"],
            "unknown_fields": sorted(payload.keys()),
        }

    payload_keys = set(payload.keys())
    unknown_fields = sorted(payload_keys - set(schema["allowed_fields"]))
    missing_required_groups = [
        sorted(group)
        for group in schema["required_any"]
        if not (payload_keys & set(group))
    ]
    empty_required_fields = sorted(
        key
        for key, value in payload.items()
        if key in set().union(*schema["required_any"]) and value in (None, "", [])
    )
    return {
        "entity_type": normalized_entity,
        "valid": not unknown_fields and not missing_required_groups and not empty_required_fields,
        "missing_required_groups": missing_required_groups,
        "empty_required_fields": empty_required_fields,
        "unknown_fields": unknown_fields,
    }


def validate_canonical_ingest_payload(entity_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    validation = validate_mapped_payload_against_canonical_schema(entity_type, payload)
    errors: list[str] = []

    missing_groups = validation.get("missing_required_groups") or []
    if missing_groups:
        errors.append(f"missing_required_groups={missing_groups}")

    empty_fields = validation.get("empty_required_fields") or []
    if empty_fields:
        errors.append(f"empty_required_fields={empty_fields}")

    unknown_fields = validation.get("unknown_fields") or []
    if unknown_fields:
        errors.append(f"unknown_fields={unknown_fields}")

    normalized_entity = entity_type.strip().upper()
    if normalized_entity == "TRANSACTION":
        amount = payload.get("amount")
        try:
            decimal_amount = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError):
            errors.append(f"amount inválido: {amount!r}")
        else:
            if decimal_amount <= 0:
                errors.append(f"amount deve ser maior que zero: {amount!r}")

        occurred_at = payload.get("occurred_at")
        if occurred_at not in (None, "", []):
            try:
                datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"occurred_at inválido: {occurred_at!r}")

    return {
        **validation,
        "valid": validation.get("valid", False) and not errors,
        "validation_errors": errors,
    }


# ──────────────────────────────────────────────────
# New connector configs (Gamma / Delta / Epsilon)
# ──────────────────────────────────────────────────

CONNECTOR_GAMMA_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorGamma",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",            "transform": "copy"},
        {"target": "player_cpf",              "source": "external_player_id",  "transform": "copy"},
        {"target": "type",                    "source": "transaction_type",    "transform": "mapEnum",
         "params": {"DEPOSIT": "DEPOSIT", "WITHDRAWAL": "WITHDRAWAL", "DEP": "DEPOSIT", "WD": "WITHDRAWAL"}},
        {"target": "amount",                  "source": "amount",              "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency",            "transform": "copy"},
        {"target": "occurred_at",             "source": "occurred_at",         "transform": "parseDate"},
        {"target": "device_id",               "source": "device_id",           "transform": "copy"},
        {"target": "method",                  "source": "instrument_type",     "transform": "copy"},
        {"target": "status",                  "source": None,                  "transform": "constant", "params": {"value": "SETTLED"}},
    ],
}

CONNECTOR_DELTA_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorDelta",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",        "transform": "copy"},
        {"target": "player_cpf",              "source": "external_player_id", "transform": "copy"},
        {"target": "type",                    "source": "transaction_type", "transform": "normalize",
         "params": {"deposit": "DEPOSIT", "dep": "DEPOSIT", "withdrawal": "WITHDRAWAL", "wd": "WITHDRAWAL", "bet": "BET"}},
        {"target": "amount",                  "source": "amount",          "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency",        "transform": "copy"},
        {"target": "occurred_at",             "source": "occurred_at",     "transform": "parseDate"},
        {"target": "device_id",               "source": "device",          "transform": "copy"},
        {"target": "method",                  "source": "pay_method",      "transform": "copy"},
        {"target": "ip_address",              "source": "ip",              "transform": "copy"},
        {"target": "session_id",              "source": "session_id",      "transform": "copy"},
        {"target": "status",                  "source": None,              "transform": "constant", "params": {"value": "SETTLED"}},
    ],
}

CONNECTOR_EPSILON_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorEpsilon",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",           "transform": "copy"},
        {"target": "player_cpf",              "source": "player_id",          "transform": "copy"},
        {"target": "type",                    "source": "event_type",         "transform": "mapEnum",
         "params": {"DEPOSIT": "DEPOSIT", "WITHDRAWAL": "WITHDRAWAL", "BET": "BET"}},
        {"target": "amount",                  "source": "gross_amount",       "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency_code",      "transform": "copy"},
        {"target": "occurred_at",             "source": "event_time",         "transform": "parseDate"},
        {"target": "device_id",               "source": "device_fingerprint", "transform": "copy"},
        {"target": "ip_address",              "source": "client_ip",          "transform": "copy"},
        {"target": "session_id",              "source": "session_token",      "transform": "copy"},
        {"target": "status",                  "source": None,                 "transform": "constant", "params": {"value": "SETTLED"}},
    ],
}

# Extend registry
BACKOFFICE_CONFIGS["ConnectorGamma:TRANSACTION"]   = CONNECTOR_GAMMA_TRANSACTION
BACKOFFICE_CONFIGS["ConnectorDelta:TRANSACTION"]   = CONNECTOR_DELTA_TRANSACTION
BACKOFFICE_CONFIGS["ConnectorEpsilon:TRANSACTION"] = CONNECTOR_EPSILON_TRANSACTION


# ──────────────────────────────────────────────────
# Version management helpers
# ──────────────────────────────────────────────────

async def activate_mapping_version(
    db,
    mapping_config_id: int,
    version_number: int,
) -> None:
    """
    Ativa uma versão específica de MappingConfig:
      1. Desmarca is_current de todas as versões do mesmo parent/grupo.
      2. Marca is_current=True na versão alvo.

    Deve ser chamada dentro de um contexto de sessão SQLAlchemy async.
    """
    from sqlalchemy import update, select
    from libs.models import MappingConfig  # lazy import to avoid circular deps

    async with db.begin():
        # Find the target row
        stmt = select(MappingConfig).where(
            MappingConfig.id == mapping_config_id,
            MappingConfig.version_number == version_number,
        )
        result = await db.execute(stmt)
        target = result.scalar_one_or_none()
        if target is None:
            raise MappingError(f"MappingConfig id={mapping_config_id} version={version_number} not found")

        # Determine group: rows with same source_system+entity_type in same tenant
        await db.execute(
            update(MappingConfig)
            .where(
                MappingConfig.tenant_id == target.tenant_id,
                MappingConfig.source_system == target.source_system,
                MappingConfig.entity_type == target.entity_type,
            )
            .values(is_current=False)
        )
        await db.execute(
            update(MappingConfig)
            .where(MappingConfig.id == mapping_config_id)
            .values(is_current=True)
        )


def clone_mapping_version(existing_config: dict[str, Any], change_notes: str = "") -> dict[str, Any]:
    """
    Clones an existing mapping config dict to create a new draft version.
    Caller must persist it (version_number incremented, is_current=False).
    """
    import copy
    new_cfg = copy.deepcopy(existing_config)
    new_cfg.pop("id", None)
    new_cfg["is_current"] = False
    new_cfg["change_notes"] = change_notes
    new_cfg["version"] = str(float(existing_config.get("version", "1.0")) + 0.1)
    return new_cfg
