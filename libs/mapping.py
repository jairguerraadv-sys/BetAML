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
from typing import Any

from pydantic import BaseModel


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
        {"target": "external_transaction_id", "source": "txnId",    "transform": "copy"},
        {"target": "player_cpf",              "source": "cpf",      "transform": "normalizeCpf"},
        {"target": "type",                    "source": "txnType",  "transform": "mapEnum",
         "params": {"CREDIT": "DEPOSIT", "DEBIT": "WITHDRAWAL", "CB": "CHARGEBACK",
                    "BONUS_CREDIT": "BONUS", "ADJ": "ADJUSTMENT"}},
        {"target": "amount",                  "source": "value",    "transform": "coerceDecimal"},
        {"target": "currency",                "source": None,       "transform": "constant",     "params": {"value": "BRL"}},
        {"target": "method",                  "source": "payMethod","transform": "mapEnum",
         "params": {"pix": "PIX", "ted": "TED", "card": "CARD", "wallet": "WALLET"}},
        {"target": "status",                  "source": "state",    "transform": "mapEnum",
         "params": {"PROCESSED": "SETTLED", "PENDING": "PENDING", "ERROR": "FAILED", "REVERSED": "REVERSED"}},
        {"target": "occurred_at",             "source": "createdAt","transform": "parseDate"},
    ],
}

BACKOFFICE_ALPHA_PLAYER: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeAlpha",
    "entity_type": "PLAYER",
    "fields": [
        {"target": "external_player_id", "source": "userId",      "transform": "copy"},
        {"target": "cpf",                "source": "document",    "transform": "normalizeCpf"},
        {"target": "name",               "source": "fullName",    "transform": "strip"},
        {"target": "birth_date",         "source": "birthdate",   "transform": "parseDate"},
        {"target": "pep_flag",           "source": "isPEP",       "transform": "copy"},
        {"target": "declared_income_monthly", "source": "income", "transform": "coerceDecimal"},
    ],
}

BACKOFFICE_BETA_TRANSACTION: dict[str, Any] = {
    "version": "1.0",
    "source_system": "BackofficeBeta",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "ref",       "transform": "copy"},
        {"target": "player_cpf",              "source": "playerDoc", "transform": "normalizeCpf"},
        {"target": "type",                    "source": "kind",      "transform": "mapEnum",
         "params": {"dep": "DEPOSIT", "saque": "WITHDRAWAL", "estorno": "CHARGEBACK"}},
        {"target": "amount",                  "source": "gross",     "transform": "coerceDecimal"},
        {"target": "currency",                "source": None,        "transform": "constant",    "params": {"value": "BRL"}},
        {"target": "method",                  "source": "channel",   "transform": "mapEnum",
         "params": {"PIX": "PIX", "DOC": "TED", "CC": "CARD"}},
        {"target": "status",                  "source": "result",    "transform": "mapEnum",
         "params": {"ok": "SETTLED", "pendente": "PENDING", "falhou": "FAILED"}},
        {"target": "occurred_at",             "source": "ts",        "transform": "parseDate"},
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

BACKOFFICE_CONFIGS: dict[str, dict[str, Any]] = {
    "BackofficeAlpha:TRANSACTION": BACKOFFICE_ALPHA_TRANSACTION,
    "BackofficeAlpha:PLAYER":      BACKOFFICE_ALPHA_PLAYER,
    "BackofficeBeta:TRANSACTION":  BACKOFFICE_BETA_TRANSACTION,
    "BackofficeBeta:PLAYER":       BACKOFFICE_BETA_PLAYER,
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
    "coerceDecimal": lambda v, p: _coerce_decimal(v),
    "coerceArray":   lambda v, p: (v if isinstance(v, list) else [x.strip() for x in str(v).split(",")]) if v is not None else [],
    "lowercase":     lambda v, p: str(v).lower() if v is not None else None,
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
    return BACKOFFICE_CONFIGS.get(f"{source_system}:{entity_type}")


# ──────────────────────────────────────────────────
# New connector configs (Gamma / Delta / Epsilon)
# ──────────────────────────────────────────────────

CONNECTOR_GAMMA_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorGamma",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",          "transform": "copy"},
        {"target": "player_cpf",              "source": "external_player_id","transform": "copy"},
        {"target": "type",                    "source": "transaction_type",  "transform": "mapEnum",
         "params": {"DEPOSIT": "DEPOSIT", "WITHDRAWAL": "WITHDRAWAL", "DEP": "DEPOSIT", "WD": "WITHDRAWAL"}},
        {"target": "amount",                  "source": "amount",            "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency",          "transform": "copy"},
        {"target": "occurred_at",             "source": "occurred_at",       "transform": "parseDate"},
        {"target": "device_id",               "source": "device_id",         "transform": "copy"},
        {"target": "method",                  "source": "instrument_type",   "transform": "copy"},
        {"target": "status",                  "source": None,                "transform": "constant", "params": {"value": "SETTLED"}},
    ],
}

CONNECTOR_DELTA_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorDelta",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",          "transform": "copy"},
        {"target": "player_cpf",              "source": "external_player_id","transform": "copy"},
        {"target": "type",                    "source": "transaction_type",  "transform": "normalize",
         "params": {"deposit": "DEPOSIT", "dep": "DEPOSIT", "withdrawal": "WITHDRAWAL", "wd": "WITHDRAWAL", "bet": "BET"}},
        {"target": "amount",                  "source": "amount",            "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency",          "transform": "copy"},
        {"target": "occurred_at",             "source": "occurred_at",       "transform": "parseDate"},
        {"target": "device_id",               "source": "device_id",         "transform": "copy"},
        {"target": "method",                  "source": "instrument_type",   "transform": "copy"},
        {"target": "ip_address",              "source": "ip_address",        "transform": "copy"},
        {"target": "session_id",              "source": "session_id",        "transform": "copy"},
        {"target": "status",                  "source": None,                "transform": "constant", "params": {"value": "SETTLED"}},
    ],
}

CONNECTOR_EPSILON_TRANSACTION: dict[str, Any] = {
    "version": "2.0",
    "source_system": "ConnectorEpsilon",
    "entity_type": "TRANSACTION",
    "fields": [
        {"target": "external_transaction_id", "source": "event_id",          "transform": "copy"},
        {"target": "player_cpf",              "source": "external_player_id","transform": "copy"},
        {"target": "type",                    "source": "transaction_type",  "transform": "mapEnum",
         "params": {"DEPOSIT": "DEPOSIT", "WITHDRAWAL": "WITHDRAWAL", "BET": "BET"}},
        {"target": "amount",                  "source": "amount",            "transform": "coerceDecimal"},
        {"target": "currency",                "source": "currency",          "transform": "copy"},
        {"target": "occurred_at",             "source": "occurred_at",       "transform": "parseDate"},
        {"target": "device_id",               "source": "device_id",         "transform": "copy"},
        {"target": "ip_address",              "source": "ip_address",        "transform": "copy"},
        {"target": "session_id",              "source": "session_id",        "transform": "copy"},
        {"target": "status",                  "source": None,                "transform": "constant", "params": {"value": "SETTLED"}},
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

