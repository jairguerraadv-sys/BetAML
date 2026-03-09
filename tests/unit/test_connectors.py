"""
tests/unit/test_connectors.py
Unit tests for libs/connectors.py  — XML, NDJSON and Webhook connectors.
"""
import hashlib
import hmac
import json
import sys
import textwrap
from pathlib import Path

import pytest

# Allow importing from project root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from connectors import ConnectorGamma, ConnectorDelta, ConnectorEpsilon, get_connector


# ── ConnectorGamma (XML) ──────────────────────────────────────────────────────

SAMPLE_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <transactions>
      <transaction>
        <id>TX001</id>
        <player_id>P001</player_id>
                <type>DEPOSIT</type>
        <amount>250.00</amount>
        <currency>BRL</currency>
                <timestamp>2026-03-09T10:00:00Z</timestamp>
      </transaction>
      <transaction>
        <id>TX002</id>
        <player_id>P002</player_id>
                <type>WITHDRAWAL</type>
        <amount>100.50</amount>
        <currency>USD</currency>
                <timestamp>2026-03-09T10:05:00Z</timestamp>
      </transaction>
    </transactions>
""")


def test_gamma_parses_xml_records():
    gm = ConnectorGamma(root_tag="transaction")
    result = gm.parse(SAMPLE_XML.encode())
    assert result.success is True
    assert len(result.records) == 2


def test_gamma_record_fields():
    gm = ConnectorGamma(root_tag="transaction")
    result = gm.parse(SAMPLE_XML.encode())
    rec = result.records[0]
    assert rec["id"] == "TX001"
    assert rec["player_id"] == "P001"
    assert rec["amount"] == 250.0


def test_gamma_invalid_xml():
    gm = ConnectorGamma(root_tag="transaction")
    result = gm.parse(b"<broken xml")
    assert result.success is False
    assert result.errors


def test_gamma_empty_xml():
    gm = ConnectorGamma(root_tag="transaction")
    result = gm.parse(b"<transactions></transactions>")
    assert result.success is True
    assert result.records == []


# ── ConnectorDelta (NDJSON) ───────────────────────────────────────────────────

SAMPLE_NDJSON = (
    '{"id":"TX001","player_id":"P001","evt_type":"DEPOSIT","amount":250.0,"ts":"2026-03-09T10:00:00Z"}\n'
    '{"id":"TX002","player_id":"P002","evt_type":"WITHDRAWAL","amount":100.5,"ts":"2026-03-09T10:01:00Z"}\n'
    '\n'  # blank line — should be ignored
)


def test_delta_parses_ndjson():
    dd = ConnectorDelta()
    result = dd.parse(SAMPLE_NDJSON.encode())
    assert result.success is True
    assert len(result.records) == 2


def test_delta_record_values():
    dd = ConnectorDelta()
    result = dd.parse(SAMPLE_NDJSON.encode())
    assert result.records[0]["id"] == "TX001"
    assert result.records[1]["amount"] == 100.5


def test_delta_handles_partial_invalid_lines():
    bad = (
        b'{"id":"TX-OK","player_id":"P001","evt_type":"DEPOSIT","amount":10.0,"ts":"2026-03-09T10:00:00Z"}\n'
        b'NOT_JSON\n'
        b'{"also": "invalid"}'
    )
    dd = ConnectorDelta()
    result = dd.parse(bad)
    # Should succeed partially — valid lines parsed, errors recorded
    assert len(result.records) >= 1
    assert result.errors  # at least one error


def test_delta_empty_payload():
    dd = ConnectorDelta()
    result = dd.parse(b"")
    assert result.success is True
    assert result.records == []


# ── ConnectorEpsilon (Webhook + HMAC) ────────────────────────────────────────

SECRET = "s3cr3t-k3y"
PAYLOAD = json.dumps([
    {
        "event_id": "evt-1",
        "player_id": "P001",
        "event_type": "DEPOSIT",
        "gross_amount": 50.0,
        "event_time": "2026-03-09T10:00:00Z",
        "currency_code": "BRL",
    }
]).encode()
SIG = "sha256=" + hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()


def test_epsilon_valid_signature():
    ep = ConnectorEpsilon(secret=SECRET)
    result = ep.parse(PAYLOAD, headers={"x-epsilon-signature": SIG})
    assert result.success is True
    assert len(result.records) == 1


def test_epsilon_invalid_signature():
    ep = ConnectorEpsilon(secret=SECRET)
    result = ep.parse(PAYLOAD, headers={"x-epsilon-signature": "sha256=badhash"})
    assert result.success is False
    assert any("signature" in e.lower() for e in result.errors)


def test_epsilon_missing_signature_header():
    ep = ConnectorEpsilon(secret=SECRET)
    result = ep.parse(PAYLOAD, headers={})
    assert result.success is False


def test_epsilon_no_secret_skips_validation():
    """When secret is None, HMAC validation is skipped."""
    ep = ConnectorEpsilon(secret=None)
    result = ep.parse(PAYLOAD, headers={})
    assert result.success is True


def test_epsilon_parses_records():
    ep = ConnectorEpsilon(secret=SECRET)
    result = ep.parse(PAYLOAD, headers={"x-epsilon-signature": SIG})
    assert result.records[0]["event_id"] == "evt-1"
    assert result.records[0]["external_player_id"] == "P001"


def test_delta_missing_required_fields_goes_to_error():
    dd = ConnectorDelta()
    # missing event timestamp and event type
    result = dd.parse(b'{"id":"TX-BAD","player_id":"P001","amount":10}')
    assert result.success is False
    assert result.failed >= 1


# ── get_connector factory ─────────────────────────────────────────────────────

def test_get_connector_gamma():
    c = get_connector("gamma", root_tag="transaction")
    assert isinstance(c, ConnectorGamma)


def test_get_connector_delta():
    c = get_connector("delta")
    assert isinstance(c, ConnectorDelta)


def test_get_connector_epsilon():
    c = get_connector("epsilon", secret="x")
    assert isinstance(c, ConnectorEpsilon)


def test_get_connector_unknown():
    with pytest.raises((KeyError, ValueError)):
        get_connector("unknown_source_system_xyz")
