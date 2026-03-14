"""
BetAML — Ingest Connectors
Gamma (XML), Delta (NDJSON), Epsilon (Webhook + HMAC), plus base class.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


# ──────────────────────────────────────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    records: list[dict[str, Any]]
    total: int
    failed: int
    errors: list[Any]   # [{line, reason, raw}] or str for auth errors

    @property
    def success(self) -> bool:
        return self.failed == 0


class BaseConnector:
    source_system: str = ""
    content_type: str = ""

    def parse(self, raw: bytes | str, *, entity_type: str = "TRANSACTION") -> ParseResult:
        raise NotImplementedError

    def validate_auth(self, headers: dict[str, str], body: bytes) -> bool:
        """Return True if auth credentials are valid. Override per connector."""
        return True


class _ConnectorRecordSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str
    external_player_id: str
    transaction_type: str
    amount: float
    occurred_at: str
    currency: str = "BRL"

    @field_validator("event_id", "external_player_id", "transaction_type", "occurred_at")
    @classmethod
    def _must_not_be_empty(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("campo obrigatório vazio")
        return value

    @field_validator("amount")
    @classmethod
    def _amount_must_be_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("amount não pode ser negativo")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_must_be_iso(cls, value: str) -> str:
        v = value.replace("Z", "+00:00")
        datetime.fromisoformat(v)
        return value


def _canonicalize_record(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize variant connector keys into canonical keys before validation."""
    mapped = dict(data)

    def _first(*keys: str) -> Any:
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        return None

    mapped.setdefault("event_id", _first("event_id", "id", "EventId", "external_event_id"))
    mapped.setdefault(
        "external_player_id",
        _first("external_player_id", "player_id", "PlayerId", "uid", "playerId"),
    )
    mapped.setdefault("transaction_type", _first("transaction_type", "type", "Type", "evt_type", "event_type"))
    mapped.setdefault("amount", _first("amount", "Amount", "val", "gross_amount", "txnAmount"))
    mapped.setdefault("occurred_at", _first("occurred_at", "Timestamp", "timestamp", "ts", "event_time", "txnTimestamp"))
    mapped.setdefault("currency", _first("currency", "Amount.currency", "ccy", "currency_code") or "BRL")
    return mapped


# ──────────────────────────────────────────────────────────────────────────────
# ConnectorGamma — XML
# ──────────────────────────────────────────────────────────────────────────────
# Expected XML envelope:
#   <Events>
#     <Transaction>
#       <EventId>...</EventId>
#       <PlayerId>...</PlayerId>
#       <Type>DEPOSIT|WITHDRAWAL</Type>
#       <Amount currency="BRL">5000.00</Amount>
#       <Timestamp>2025-01-01T12:00:00Z</Timestamp>
#       <Instrument>
#         <Type>BOLETO|PIX|CARD</Type>
#         <Token>xxx</Token>
#       </Instrument>
#       <DeviceId>...</DeviceId>
#     </Transaction>
#     ...
#   </Events>
# ──────────────────────────────────────────────────────────────────────────────

_XML_MAP = {
    "EventId":   "event_id",
    "PlayerId":  "player_id",
    "Type":      "transaction_type",
    "Timestamp": "occurred_at",
    "DeviceId":  "device_id",
}

_AMOUNT_RE = re.compile(r"^\d+(\.\d+)?$")


def _xml_element_to_dict(el: ET.Element) -> dict[str, Any]:
    """Recursively turns an XML element into a flat dict with dot notation."""
    result: dict[str, Any] = {}
    tag = el.tag.split("}")[-1]  # strip namespace
    for attr_name, attr_val in el.attrib.items():
        result[f"{tag}.{attr_name}"] = attr_val
    if list(el):  # has children
        child_d: dict[str, Any] = {}
        for child in el:
            child_tag = child.tag.split("}")[-1]
            child_d[child_tag] = _xml_element_to_dict(child).get(child_tag) or child.text
        result[tag] = child_d
    else:
        result[tag] = el.text
    return result


class ConnectorGamma(BaseConnector):
    source_system = "ConnectorGamma"
    content_type  = "application/xml"

    def __init__(self, root_tag: str = ""):
        # Allow callers to specify the root element tag (case-insensitive match)
        self._root_tag = root_tag.lower() if root_tag else ""

    @property
    def _active_tags(self) -> set[str]:
        tags = self.TRANSACTION_TAGS | self.BET_TAGS
        if self._root_tag:
            tags = tags | {self._root_tag, self._root_tag.capitalize(), self._root_tag.title()}
        return tags

    # canonical field mapping: XML path → canonical key
    FIELD_MAP: dict[str, str] = {
        "EventId":                "event_id",
        "PlayerId":               "external_player_id",
        "Type":                   "transaction_type",
        "Timestamp":              "occurred_at",
        "DeviceId":               "device_id",
        "Amount":                 "amount",
        "Amount.currency":        "currency",
        "Instrument.Type":        "instrument_type",
        "Instrument.Token":       "instrument_token",
    }
    TRANSACTION_TAGS = {"Transaction"}
    BET_TAGS         = {"Bet", "BetEvent"}

    def parse(self, raw: bytes | str, *, entity_type: str = "TRANSACTION") -> ParseResult:
        if isinstance(raw, str):
            raw = raw.encode()
        records: list[dict] = []
        errors:  list[dict] = []
        line = 0

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            return ParseResult([], 0, 1, [{"line": 0, "reason": str(exc), "raw": raw[:200].decode(errors="replace")}])

        for child in root:
            line += 1
            tag = child.tag.split("}")[-1]
            if tag.lower() not in {t.lower() for t in self._active_tags}:
                continue
            try:
                flat = self._flatten(child)
                normalized = _canonicalize_record(flat)
                schema = _ConnectorRecordSchema.model_validate(normalized)
                records.append({**flat, **schema.model_dump()})
            except Exception as exc:  # noqa: BLE001
                errors.append({"line": line, "reason": str(exc), "raw": ET.tostring(child, encoding="unicode")[:300]})

        return ParseResult(records, line, len(errors), errors)

    def _flatten(self, el: ET.Element) -> dict[str, Any]:
        """Flatten element children to canonical dict. Unmapped fields keep their raw key."""
        result: dict[str, Any] = {}
        for child in el:
            ctag = child.tag.split("}")[-1]
            if len(list(child)):
                for subchild in child:
                    stag = subchild.tag.split("}")[-1]
                    path = f"{ctag}.{stag}"
                    canon = self.FIELD_MAP.get(path)
                    key = canon if canon else path
                    result[key] = subchild.text
            else:
                canon = self.FIELD_MAP.get(ctag)
                val: Any = child.text
                # coerce Amount.currency from attribute
                if ctag == "Amount":
                    result[self.FIELD_MAP.get("Amount.currency", "currency")] = child.get("currency", "BRL")
                # store under canonical name AND original tag name
                result[canon if canon else ctag] = val
        # defaults
        result.setdefault("currency", "BRL")
        result.setdefault("source_system", self.source_system)
        return result


# ──────────────────────────────────────────────────────────────────────────────
# ConnectorDelta — NDJSON (Newline-delimited JSON)
# ──────────────────────────────────────────────────────────────────────────────
# One JSON object per line. Flexible schema, fields mapped via config.
# ──────────────────────────────────────────────────────────────────────────────

class ConnectorDelta(BaseConnector):
    source_system = "ConnectorDelta"
    content_type  = "application/x-ndjson"

    # Default field map for ConnectorDelta native format
    FIELD_MAP: dict[str, str] = {
        "id":             "event_id",
        "uid":            "external_player_id",
        "evt_type":       "transaction_type",
        "ts":             "occurred_at",
        "val":            "amount",
        "ccy":            "currency",
        "device":         "device_id",
        "pay_method":     "instrument_type",
        "pay_token":      "instrument_token",
        "ip":             "ip_address",
        "session_id":     "session_id",
        "odds":           "odds",
        "outcome":        "outcome",
        "stake":          "stake_amount",
        "sport":          "sport",
        "market":         "market",
    }

    def parse(self, raw: bytes | str, *, entity_type: str = "TRANSACTION") -> ParseResult:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        records: list[dict] = []
        errors:  list[dict] = []

        for line_num, line in enumerate(raw.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                mapped = self._map(obj)
                normalized = _canonicalize_record(mapped)
                schema = _ConnectorRecordSchema.model_validate(normalized)
                records.append({**mapped, **schema.model_dump()})
            except (json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
                errors.append({"line": line_num, "reason": str(exc), "raw": line[:300]})

        return ParseResult(records, line_num if raw.strip() else 0, len(errors), errors)  # type: ignore[possibly-undefined]

    def _map(self, obj: dict) -> dict:
        result: dict[str, Any] = {"source_system": self.source_system}
        for src_key, canon_key in self.FIELD_MAP.items():
            if src_key in obj:
                result[canon_key] = obj[src_key]
        # Pass through ALL original fields (preserves raw keys alongside canonical)
        for k, v in obj.items():
            result.setdefault(k, v)
        result.setdefault("currency", "BRL")
        return result


# ──────────────────────────────────────────────────────────────────────────────
# ConnectorEpsilon — Webhook with HMAC-SHA256 signature validation
# ──────────────────────────────────────────────────────────────────────────────
# Header: X-Epsilon-Signature: sha256=<hex>
# Webhook payload: JSON with envelope { "version": "1.0", "events": [...] }
# ──────────────────────────────────────────────────────────────────────────────

EPSILON_SIGNATURE_HEADER = "x-epsilon-signature"
EPSILON_TIMESTAMP_HEADER = "x-epsilon-timestamp"


class ConnectorEpsilon(BaseConnector):
    source_system = "ConnectorEpsilon"
    content_type  = "application/json"

    FIELD_MAP: dict[str, str] = {
        "event_id":          "event_id",
        "player_id":         "external_player_id",
        "event_type":        "transaction_type",
        "event_time":        "occurred_at",
        "gross_amount":      "amount",
        "currency_code":     "currency",
        "device_fingerprint":"device_id",
        "payment_method":    "instrument_type",
        "payment_reference": "instrument_token",
        "client_ip":         "ip_address",
        "session_token":     "session_id",
        "bet_odds":          "odds",
        "bet_outcome":       "outcome",
        "bet_stake":         "stake_amount",
        "sport_category":    "sport",
        "market_name":       "market",
    }

    def __init__(self, signing_secret: str = "", secret: str | None = None):
        # Accept `secret` kwarg as alias for `signing_secret`
        self.signing_secret = secret if secret is not None else signing_secret

    def validate_auth(self, headers: dict[str, str], body: bytes) -> bool:
        """Validate HMAC-SHA256 signature.
        
        Expected header value: ``sha256=<hex_digest>``
        """
        if not self.signing_secret:
            return True  # bypass in dev if no secret configured

        sig_header = headers.get(EPSILON_SIGNATURE_HEADER, "")
        if not sig_header.startswith("sha256="):
            return False

        received_hex = sig_header[len("sha256="):]
        # optionally include timestamp in signed content to prevent replay
        ts = headers.get(EPSILON_TIMESTAMP_HEADER, "")
        signed_body = (ts + ".").encode() + body if ts else body
        expected = hmac.new(
            self.signing_secret.encode(),
            signed_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, received_hex)

    def parse(self, raw: bytes | str, *, entity_type: str = "TRANSACTION",
               headers: dict[str, str] | None = None) -> ParseResult:
        body = raw if isinstance(raw, bytes) else raw.encode("utf-8", errors="replace")

        # Validate HMAC signature when headers are provided
        if headers is not None:
            if not self.validate_auth(headers, body):
                return ParseResult([], 0, 1, ["Invalid signature"])

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        records: list[dict] = []
        errors:  list[Any] = []

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return ParseResult([], 0, 1, [{"line": 0, "reason": str(exc), "raw": raw[:300]}])

        # Support both {events:[...]} envelope and bare array
        if isinstance(payload, list):
            events = payload
        elif isinstance(payload, dict):
            events = payload.get("events", [payload])
        else:
            return ParseResult([], 0, 1, [{"line": 0, "reason": "Unexpected payload type", "raw": raw[:300]}])

        for i, evt in enumerate(events):
            try:
                mapped = self._map(evt)
                normalized = _canonicalize_record(mapped)
                schema = _ConnectorRecordSchema.model_validate(normalized)
                records.append({**mapped, **schema.model_dump()})
            except Exception as exc:  # noqa: BLE001
                errors.append({"line": i + 1, "reason": str(exc), "raw": json.dumps(evt)[:300]})

        return ParseResult(records, len(events), len(errors), errors)

    def _map(self, obj: dict) -> dict:
        result: dict[str, Any] = {"source_system": self.source_system}
        for src_key, canon_key in self.FIELD_MAP.items():
            if src_key in obj:
                result[canon_key] = obj[src_key]
        # Pass through ALL original fields (raw keys alongside canonical)
        for k, v in obj.items():
            result.setdefault(k, v)
        result.setdefault("currency", "BRL")
        return result


# ──────────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────────

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    "ConnectorGamma":   ConnectorGamma,
    "ConnectorDelta":   ConnectorDelta,
    "ConnectorEpsilon": ConnectorEpsilon,
    # lowercase aliases for convenience
    "gamma":            ConnectorGamma,
    "delta":            ConnectorDelta,
    "epsilon":          ConnectorEpsilon,
}


def get_connector(source_system: str, **kwargs: Any) -> BaseConnector:
    """Return an instantiated connector for the given source_system name."""
    cls = CONNECTOR_REGISTRY.get(source_system)
    if cls is None:
        raise ValueError(f"Unknown connector: {source_system!r}")
    return cls(**kwargs)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────────
# MappingConfig YAML templates for each new connector
# ──────────────────────────────────────────────────────────────────────────────

GAMMA_TEMPLATE_YAML = """\
# ConnectorGamma — XML Transaction mapping template
source_system: ConnectorGamma
entity_type: TRANSACTION
connector: xml
transforms:
  - field: event_id
    type: copy
    source: event_id
  - field: external_player_id
    type: copy
    source: external_player_id
  - field: transaction_type
    type: mapEnum
    source: transaction_type
    mapping:
      DEP: DEPOSIT
      WD:  WITHDRAWAL
      BET: BET
  - field: amount
    type: coerceDecimal
    source: amount
  - field: occurred_at
    type: parseDate
    source: occurred_at
    format: iso8601
  - field: currency
    type: copy
    source: currency
  - field: device_id
    type: copy
    source: device_id
  - field: instrument_type
    type: copy
    source: instrument_type
  - field: instrument_token
    type: copy
    source: instrument_token
"""

DELTA_TEMPLATE_YAML = """\
# ConnectorDelta — NDJSON Transaction mapping template
source_system: ConnectorDelta
entity_type: TRANSACTION
connector: ndjson
transforms:
  - field: event_id
    type: copy
    source: event_id
  - field: external_player_id
    type: copy
    source: external_player_id
  - field: transaction_type
    type: normalize
    source: transaction_type
    mapping:
      dep:        DEPOSIT
      withdrawal: WITHDRAWAL
      wd:         WITHDRAWAL
      bet:        BET
  - field: amount
    type: coerceDecimal
    source: amount
  - field: occurred_at
    type: parseDate
    source: occurred_at
    format: iso8601
  - field: currency
    type: copy
    source: currency
  - field: device_id
    type: copy
    source: device_id
"""

EPSILON_TEMPLATE_YAML = """\
# ConnectorEpsilon — Webhook HMAC JSON mapping template
source_system: ConnectorEpsilon
entity_type: TRANSACTION
connector: webhook_hmac
hmac_header: x-epsilon-signature
transforms:
  - field: event_id
    type: copy
    source: event_id
  - field: external_player_id
    type: copy
    source: external_player_id
  - field: transaction_type
    type: mapEnum
    source: transaction_type
    mapping:
      DEPOSIT:    DEPOSIT
      WITHDRAWAL: WITHDRAWAL
      BET:        BET
  - field: amount
    type: coerceDecimal
    source: amount
  - field: occurred_at
    type: parseDate
    source: occurred_at
    format: iso8601
  - field: currency
    type: copy
    source: currency
  - field: ip_address
    type: copy
    source: ip_address
"""


CONNECTOR_TEMPLATE_REGISTRY: dict[str, dict[str, Any]] = {
    "ConnectorGamma": {
        "connector_name": "gamma",
        "source_system": "ConnectorGamma",
        "format": "yaml",
        "content_type": "application/xml",
        "payload_format": "xml",
        "auth_mode": "none",
        "template": GAMMA_TEMPLATE_YAML,
        "sample_payload": """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<transactions>
  <transaction>
    <id>TXG-9001</id>
    <player_id>PLY-G-100</player_id>
    <type>DEPOSIT</type>
    <amount>1200.50</amount>
    <currency>BRL</currency>
    <timestamp>2026-03-09T10:00:00Z</timestamp>
    <device_id>device-gamma-01</device_id>
  </transaction>
</transactions>
""",
        "input_schema": [
            {"name": "id", "type": "string", "required": True, "description": "Identificador externo do evento"},
            {"name": "player_id", "type": "string", "required": True, "description": "Identificador externo do jogador"},
            {"name": "type", "type": "string", "required": True, "description": "Tipo transacional no XML de origem"},
            {"name": "amount", "type": "number", "required": True, "description": "Valor monetário da transação"},
            {"name": "timestamp", "type": "datetime", "required": True, "description": "Data/hora ISO8601 do evento"},
            {"name": "currency", "type": "string", "required": False, "description": "Moeda da transação; default BRL"},
            {"name": "device_id", "type": "string", "required": False, "description": "Identificador do dispositivo"},
        ],
    },
    "ConnectorDelta": {
        "connector_name": "delta",
        "source_system": "ConnectorDelta",
        "format": "yaml",
        "content_type": "application/x-ndjson",
        "payload_format": "ndjson",
        "auth_mode": "none",
        "template": DELTA_TEMPLATE_YAML,
        "sample_payload": """{\"id\":\"TXD-1\",\"player_id\":\"PLY-D-1\",\"evt_type\":\"DEPOSIT\",\"amount\":500.0,\"ts\":\"2026-03-09T10:01:00Z\"}
{\"id\":\"TXD-2\",\"player_id\":\"PLY-D-2\",\"evt_type\":\"WITHDRAWAL\",\"amount\":100.0,\"ts\":\"2026-03-09T10:02:00Z\"}
""",
        "input_schema": [
            {"name": "id", "type": "string", "required": True, "description": "Identificador externo do evento"},
            {"name": "player_id", "type": "string", "required": True, "description": "Identificador externo do jogador"},
            {"name": "evt_type", "type": "string", "required": True, "description": "Tipo transacional em formato NDJSON"},
            {"name": "amount", "type": "number", "required": True, "description": "Valor monetário da linha"},
            {"name": "ts", "type": "datetime", "required": True, "description": "Timestamp ISO8601 do evento"},
            {"name": "currency", "type": "string", "required": False, "description": "Moeda opcional"},
            {"name": "device_id", "type": "string", "required": False, "description": "Dispositivo associado ao evento"},
        ],
    },
    "ConnectorEpsilon": {
        "connector_name": "epsilon",
        "source_system": "ConnectorEpsilon",
        "format": "yaml",
        "content_type": "application/json",
        "payload_format": "webhook-json",
        "auth_mode": "hmac_sha256",
        "signature_header": EPSILON_SIGNATURE_HEADER,
        "timestamp_header": EPSILON_TIMESTAMP_HEADER,
        "template": EPSILON_TEMPLATE_YAML,
        "sample_payload": json.dumps(
            {
                "events": [
                    {
                        "event_id": "evt-eps-100",
                        "player_id": "PLY-EPS-1",
                        "event_type": "DEPOSIT",
                        "gross_amount": 999.9,
                        "event_time": "2026-03-09T10:03:00Z",
                        "currency_code": "BRL",
                    }
                ]
            },
            indent=2,
        ),
        "input_schema": [
            {"name": "events[].event_id", "type": "string", "required": True, "description": "Identificador do evento entregue no webhook"},
            {"name": "events[].player_id", "type": "string", "required": True, "description": "Identificador do jogador"},
            {"name": "events[].event_type", "type": "string", "required": True, "description": "Tipo do evento transacional"},
            {"name": "events[].gross_amount", "type": "number", "required": True, "description": "Valor bruto recebido"},
            {"name": "events[].event_time", "type": "datetime", "required": True, "description": "Timestamp ISO8601 do evento"},
            {"name": "events[].currency_code", "type": "string", "required": False, "description": "Moeda opcional; default BRL"},
        ],
    },
}
