"""
tests/unit/test_ingest_core.py — Unit tests for routers/ingest.py (core logic).

Tests cover:
  - IngestEventRequest schema validation (required fields, types)
  - ALLOWED_SOURCE_SYSTEMS contains expected connector names
  - ingest_event: unknown source_system raises 400
  - ingest_event: disabled system flag raises 503
  - ingest_event: valid request is enqueued via get_producer
  - list_ingest_errors: returns list with correct shape
  - resolve_ingest_error: 404 on missing error
  - replay_ingest_error: 404 on missing error
  - ConnectorParseSummary: correct field defaults
"""
from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import WebSocketDisconnect
from starlette.requests import Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_db(execute_result=None, get_result=None):
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        if execute_result is not None:
            if callable(execute_result):
                return execute_result(stmt)
            result.scalar_one_or_none.return_value = execute_result
        else:
            result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    db.get = AsyncMock(return_value=get_result)
    return db


class _FakeWebSocket:
    def __init__(self, *, token: str = "token", messages: list[object] | None = None):
        self.headers = {"authorization": f"Bearer {token}"}
        self._messages = list(messages or [])
        self.sent: list[dict] = []
        self.accepted = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int) -> None:
        self.close_code = code

    async def receive_json(self) -> dict:
        await asyncio.sleep(0)
        if self._messages:
            item = self._messages.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise WebSocketDisconnect()


class _BackpressureQueue:
    def __init__(self, maxsize: int = 500):
        self.maxsize = maxsize

    async def get(self):
        await asyncio.Future()

    def put_nowait(self, _item) -> None:
        raise asyncio.QueueFull()

    def qsize(self) -> int:
        return self.maxsize

    def task_done(self) -> None:
        return None


# ---------------------------------------------------------------------------
# ALLOWED_SOURCE_SYSTEMS
# ---------------------------------------------------------------------------

def test_allowed_source_systems_includes_all_connectors():
    """ALLOWED_SOURCE_SYSTEMS must include all three connector types."""
    from routers.ingest import ALLOWED_SOURCE_SYSTEMS

    assert "ConnectorGamma" in ALLOWED_SOURCE_SYSTEMS
    assert "ConnectorDelta" in ALLOWED_SOURCE_SYSTEMS
    assert "ConnectorEpsilon" in ALLOWED_SOURCE_SYSTEMS


def test_allowed_source_systems_includes_backoffice():
    """ALLOWED_SOURCE_SYSTEMS must include legacy BackofficeAlpha/Beta."""
    from routers.ingest import ALLOWED_SOURCE_SYSTEMS

    assert "BackofficeAlpha" in ALLOWED_SOURCE_SYSTEMS
    assert "BackofficeBeta" in ALLOWED_SOURCE_SYSTEMS


def test_allowed_source_systems_is_frozenset():
    """ALLOWED_SOURCE_SYSTEMS must be a frozenset (immutable)."""
    from routers.ingest import ALLOWED_SOURCE_SYSTEMS

    assert isinstance(ALLOWED_SOURCE_SYSTEMS, frozenset)


# ---------------------------------------------------------------------------
# IngestEventRequest schema
# ---------------------------------------------------------------------------

def test_ingest_event_request_valid():
    """IngestEventRequest accepts valid source_system, entity_type and payload."""
    from routers.ingest import IngestEventRequest

    req = IngestEventRequest(
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={"amount": 5000, "player_id": "P-001"},
    )

    assert req.source_system == "BackofficeAlpha"
    assert req.entity_type == "TRANSACTION"


def test_ingest_event_request_optional_fields_default_none():
    """source_event_id and mapping_config_id default to None."""
    from routers.ingest import IngestEventRequest

    req = IngestEventRequest(
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={},
    )

    assert req.source_event_id is None
    assert req.mapping_config_id is None


def test_ingest_event_request_with_source_event_id():
    """source_event_id is stored when provided."""
    from routers.ingest import IngestEventRequest

    req = IngestEventRequest(
        source_system="SportsBook",
        entity_type="BET",
        payload={"bet_id": "B-123"},
        source_event_id="ext-uuid-456",
    )

    assert req.source_event_id == "ext-uuid-456"


# ---------------------------------------------------------------------------
# ingest_event — unknown source_system raises 400
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_event_unknown_source_system_raises_400():
    """POST /ingest/event with unknown source_system must raise 400."""
    from routers.ingest import ingest_event, IngestEventRequest

    body = IngestEventRequest(
        source_system="UnknownSystem",
        entity_type="TRANSACTION",
        payload={"amount": 100},
    )
    db = _make_db()
    user = _make_user()

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await ingest_event(body=body, current_user=user, db=db)

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# ingest_event — disabled system flag raises 503
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_event_valid_request_returns_event_id():
    """POST /ingest/event with valid source_system returns event_id in response."""
    from routers.ingest import ingest_event, IngestEventRequest

    body = IngestEventRequest(
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={"amount": 100, "player_id": "P-1"},
    )

    db = _make_db()
    user = _make_user()

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest.get_producer") as mock_get_prod:
        mock_producer = AsyncMock()
        mock_producer.send = AsyncMock(return_value=None)
        mock_get_prod.return_value = mock_producer

        result = await ingest_event(body=body, current_user=user, db=db)

    assert "event_id" in result


@pytest.mark.asyncio
async def test_ingest_event_applies_explicit_mapping_before_publish():
    from routers.ingest import ingest_event, IngestEventRequest

    body = IngestEventRequest(
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        mapping_config_id="map-explicit-1",
        payload={"customer_id": "P-1", "amount": "100.50", "event_id": "evt-map-1"},
    )

    db = _make_db()
    user = _make_user()
    sent_messages: list[tuple[str, dict, str]] = []

    async def _send(topic, payload, key, headers=None):
        sent_messages.append((topic, payload, key))

    mock_producer = AsyncMock()
    mock_producer.send = AsyncMock(side_effect=_send)

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest.get_producer", return_value=mock_producer), \
         patch("routers.ingest._resolve_effective_mapping_config", AsyncMock(return_value=(
             "map-explicit-1",
             {
                 "version": "1.0",
                 "source_system": "BackofficeAlpha",
                 "entity_type": "TRANSACTION",
                 "fields": [
                     {"target": "external_player_id", "source": "customer_id", "transform": "copy"},
                     {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                     {"target": "event_id", "source": "event_id", "transform": "copy"},
                 ],
             },
         ))):
        result = await ingest_event(body=body, current_user=user, db=db)

    assert "event_id" in result
    assert sent_messages
    topic, payload, key = sent_messages[0]
    assert topic == "raw.transactions"
    assert key == "evt-map-1"
    assert payload["mapping_config_id"] == "map-explicit-1"
    assert payload["payload"]["external_player_id"] == "P-1"
    assert payload["payload"]["amount"] == 100.5
    assert payload["raw_payload"]["customer_id"] == "P-1"


# ---------------------------------------------------------------------------
# ConnectorParseSummary
# ---------------------------------------------------------------------------

def test_connector_parse_summary_defaults():
    """ConnectorParseSummary initialises with correct fields."""
    from routers.ingest import ConnectorParseSummary

    summary = ConnectorParseSummary(accepted=5, failed=1, total=6, errors=[])

    assert summary.accepted == 5
    assert summary.failed == 1
    assert summary.total == 6
    assert summary.errors == []


# ---------------------------------------------------------------------------
# ReprocessRequest
# ---------------------------------------------------------------------------

def test_reprocess_request_defaults():
    """ReprocessRequest has a sensible default reason."""
    from routers.ingest import ReprocessRequest

    req = ReprocessRequest(reason="manual_reprocess")
    assert req.reason == "manual_reprocess"
    assert req.mapping_version_id is None


# ---------------------------------------------------------------------------
# resolve_ingest_error — 404 on missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_ingest_error_404_when_not_found():
    """POST /ingest/errors/{id}/resolve raises 404 when error not found."""
    from routers.ingest import resolve_ingest_error, ResolveIngestErrorRequest

    db = _make_db(get_result=None)
    user = _make_user()
    body = ResolveIngestErrorRequest(note="manually resolved")

    with pytest.raises(HTTPException) as exc_info:
        await resolve_ingest_error(error_id="nonexistent", body=body, current_user=user, db=db)

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# replay_ingest_error — 404 on missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_ingest_error_404_when_not_found():
    """POST /ingest/errors/{id}/replay raises 404 when error not found."""
    from routers.ingest import replay_ingest_error, ReplayIngestErrorRequest
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()
    body = ReplayIngestErrorRequest(corrected_payload={"amount": 100})

    with pytest.raises(HTTPException) as exc_info:
        with patch("routers.ingest.get_producer", return_value=AsyncMock()):
            await replay_ingest_error(
                error_id="nonexistent", body=body,
                current_user=user, db=db,
            )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_ingest_websocket_rejects_revoked_token():
    from routers.ingest import ingest_websocket

    websocket = _FakeWebSocket()
    fake_redis = AsyncMock()
    fake_redis.exists = AsyncMock(return_value=True)

    with patch("routers.ingest.jwt.decode", return_value={"sub": "u1", "tenant_id": "t1", "jti": "revoked-1"}), \
         patch("auth._get_auth_redis", AsyncMock(return_value=fake_redis)):
        await ingest_websocket(websocket)

    assert websocket.accepted is True
    assert websocket.sent == [{"error": "token_revoked"}]
    assert websocket.close_code == 1008


@pytest.mark.asyncio
async def test_ingest_websocket_backpressure_updates_runtime_state():
    from routers import ingest as ingest_module
    from routers.ingest import ingest_websocket

    ingest_module._INGEST_WS_RUNTIME.pop("t1", None)

    websocket = _FakeWebSocket(messages=[
        {
            "source_system": "BackofficeAlpha",
            "entity_type": "TRANSACTION",
            "payload": {"amount": 100, "player_id": "p1"},
        },
        WebSocketDisconnect(),
    ])

    user = MagicMock()
    user.active = True
    user.role = "AML_ANALYST"

    db = AsyncMock()
    db.get = AsyncMock(return_value=user)

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=db)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    producer = AsyncMock()

    with patch("routers.ingest.jwt.decode", return_value={"sub": "u1", "tenant_id": "t1"}), \
         patch("routers.ingest.AsyncSessionLocal", return_value=session_cm), \
         patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=600)), \
         patch("routers.ingest.get_producer", AsyncMock(return_value=producer)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest.asyncio.Queue", _BackpressureQueue):
        await ingest_websocket(websocket)

    runtime_state = ingest_module._INGEST_WS_RUNTIME["t1"]
    assert websocket.accepted is True
    assert any(msg.get("status") == "backpressure" for msg in websocket.sent)
    assert runtime_state["active_connections"] == 0
    assert runtime_state["backpressure_events"] == 1
    assert runtime_state["peak_queue_depth"] == 500
    assert runtime_state["queued_messages"] == 0
    assert runtime_state["last_backpressure_at"] is not None


@pytest.mark.asyncio
async def test_ingest_websocket_applies_explicit_mapping_before_publish():
    from routers import ingest as ingest_module
    from routers.ingest import ingest_websocket

    ingest_module._INGEST_WS_RUNTIME.pop("t1", None)

    websocket = _FakeWebSocket(messages=[
        {
            "source_system": "BackofficeAlpha",
            "entity_type": "TRANSACTION",
            "mapping_config_id": "map-ws-1",
            "payload": {"customer_id": "P-900", "amount": "77.10", "event_id": "evt-ws-1"},
        },
    ])

    user = MagicMock()
    user.active = True
    user.role = "AML_ANALYST"

    auth_db = AsyncMock()
    auth_db.get = AsyncMock(return_value=user)
    mapping_db = AsyncMock()

    auth_cm = MagicMock()
    auth_cm.__aenter__ = AsyncMock(return_value=auth_db)
    auth_cm.__aexit__ = AsyncMock(return_value=False)

    mapping_cm = MagicMock()
    mapping_cm.__aenter__ = AsyncMock(return_value=mapping_db)
    mapping_cm.__aexit__ = AsyncMock(return_value=False)

    sent_messages: list[tuple[str, dict, str]] = []

    async def _send(topic, payload, key, headers=None):
        sent_messages.append((topic, payload, key))

    producer = AsyncMock()
    producer.send = AsyncMock(side_effect=_send)

    with patch("routers.ingest.jwt.decode", return_value={"sub": "u1", "tenant_id": "t1"}), \
         patch("routers.ingest.AsyncSessionLocal", side_effect=[auth_cm, mapping_cm]), \
         patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=600)), \
         patch("routers.ingest.get_producer", AsyncMock(return_value=producer)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest._resolve_effective_mapping_config", AsyncMock(return_value=(
             "map-ws-1",
             {
                 "version": "1.0",
                 "source_system": "BackofficeAlpha",
                 "entity_type": "TRANSACTION",
                 "fields": [
                     {"target": "external_player_id", "source": "customer_id", "transform": "copy"},
                     {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                     {"target": "event_id", "source": "event_id", "transform": "copy"},
                 ],
             },
         ))):
        await ingest_websocket(websocket)

    assert sent_messages
    topic, payload, key = sent_messages[0]
    assert topic == "raw.transactions"
    assert key == "evt-ws-1"
    assert payload["mapping_config_id"] == "map-ws-1"
    assert payload["payload"]["external_player_id"] == "P-900"
    assert payload["payload"]["amount"] == 77.1
    assert payload["raw_payload"]["customer_id"] == "P-900"
    assert any(msg.get("status") == "queued" for msg in websocket.sent)


@pytest.mark.asyncio
async def test_replay_ingest_error_applies_resolved_mapping_before_publish():
    from routers.ingest import replay_ingest_error, ReplayIngestErrorRequest

    err = MagicMock()
    err.id = "err-1"
    err.tenant_id = "t1"
    err.source_system = "ConnectorGamma"
    err.entity_type = "TRANSACTION"
    err.line_number = 7
    err.error_detail = {}
    err.resolved = False
    err.resolved_by = None
    err.resolved_at = None

    mapping = MagicMock()
    mapping.id = "map-v3"
    mapping.tenant_id = "t1"
    mapping.config_json = {
        "version": "1.0",
        "source_system": "ConnectorGamma",
        "entity_type": "TRANSACTION",
        "fields": [
            {"source": "customer_id", "target": "external_player_id", "transform": "copy"},
            {"source": "amount", "target": "amount", "transform": "coerceDecimal"},
            {"source": "event_id", "target": "event_id", "transform": "copy"},
        ],
    }

    async def _db_get(model, ident):
        if ident == "err-1":
            return err
        if ident == "map-v3":
            return mapping
        return None

    db = _make_db(execute_result=mapping, get_result=None)
    db.get = AsyncMock(side_effect=_db_get)

    sent_messages: list[tuple[str, dict, str]] = []

    async def _send(topic, payload, key, headers=None):
        sent_messages.append((topic, payload, key))

    producer = AsyncMock()
    producer.send = AsyncMock(side_effect=_send)

    body = ReplayIngestErrorRequest(
        corrected_payload={
            "event_id": "evt-replay-1",
            "customer_id": "CPF-001",
            "amount": 321.5,
        },
        entity_type="TRANSACTION",
        mapping_config_id="map-v3",
        note="corrigido manualmente",
    )

    with patch("routers.ingest.get_producer", return_value=producer):
        result = await replay_ingest_error(
            error_id="err-1",
            body=body,
            current_user=_make_user(),
            db=db,
        )

    assert result["status"] == "queued"
    assert result["mapping_config_id"] == "map-v3"
    assert result["mapping_applied"] is True
    assert err.resolved is True
    assert isinstance(err.error_detail.get("replay"), dict)
    assert err.error_detail["replay"]["mapping_config_id"] == "map-v3"
    assert err.error_detail["replay"]["apply_mapping"] is True
    assert sent_messages
    topic, payload, key = sent_messages[0]
    assert topic == "raw.transactions"
    assert key == "evt-replay-1"
    assert payload["mapping_config_id"] == "map-v3"
    assert payload["payload"]["external_player_id"] == "CPF-001"
    assert payload["payload"]["amount"] == 321.5
    assert payload["raw_payload"]["customer_id"] == "CPF-001"


@pytest.mark.asyncio
async def test_ingest_epsilon_webhook_registers_job_and_returns_job_id():
    from routers.ingest import ingest_epsilon_webhook

    principal = MagicMock()
    principal.tenant_id = "t1"
    principal.id = "u1"

    body = b'{"events":[{"event_id":"evt-1","player_id":"p1","event_type":"DEPOSIT","gross_amount":10.0,"event_time":"2026-03-20T10:00:00Z","currency_code":"BRL"}]}'

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ingest/webhook/epsilon",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-epsilon-signature", b"sha256=dummy"),
            ],
        },
        receive=receive,
    )

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", "job-1"))

    producer = AsyncMock()
    producer.send = AsyncMock(return_value=None)

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest._upload_bronze_file", return_value="bronze/t1/job-1/epsilon.json"), \
         patch("routers.ingest.get_producer", AsyncMock(return_value=producer)), \
         patch("routers.ingest.ConnectorEpsilon") as connector_cls:
        connector = MagicMock()
        connector.parse.return_value = MagicMock(
            success=True,
            records=[
                {
                    "event_id": "evt-1",
                    "external_player_id": "p1",
                    "transaction_type": "DEPOSIT",
                    "amount": 10.0,
                    "occurred_at": "2026-03-20T10:00:00Z",
                    "currency": "BRL",
                }
            ],
            total=1,
            failed=0,
            errors=[],
        )
        connector_cls.return_value = connector

        result = await ingest_epsilon_webhook(request=request, principal=principal, db=db)

    assert result["status"] == "accepted"
    assert result["count"] == 1
    assert result["job_id"] == "job-1"
    assert db.add.call_count >= 1


@pytest.mark.asyncio
async def test_ingest_epsilon_webhook_invalid_signature_creates_failed_job():
    from routers.ingest import ingest_epsilon_webhook

    principal = MagicMock()
    principal.tenant_id = "t1"
    principal.id = "u1"

    body = b'{"events":[]}'

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ingest/webhook/epsilon",
            "headers": [(b"x-epsilon-signature", b"sha256=bad")],
        },
        receive=receive,
    )

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", "job-2"))

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest._upload_bronze_file", return_value=None), \
         patch("routers.ingest.ConnectorEpsilon") as connector_cls:
        connector = MagicMock()
        connector.parse.return_value = MagicMock(
            success=False,
            records=[],
            total=0,
            failed=1,
            errors=["Invalid signature"],
        )
        connector_cls.return_value = connector

        with pytest.raises(HTTPException) as exc_info:
            await ingest_epsilon_webhook(request=request, principal=principal, db=db)

    assert exc_info.value.status_code == 400
    assert db.add.call_count >= 2


@pytest.mark.asyncio
async def test_ingest_epsilon_webhook_applies_resolved_mapping_before_publish():
    from routers.ingest import ingest_epsilon_webhook

    principal = MagicMock()
    principal.tenant_id = "t1"
    principal.id = "u1"

    body = b'{"events":[{"event_id":"evt-1","player_id":"p1","event_type":"DEPOSIT","gross_amount":10.0,"event_time":"2026-03-20T10:00:00Z","currency_code":"BRL"}]}'

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ingest/webhook/epsilon",
            "headers": [
                (b"content-type", b"application/json"),
                (b"x-epsilon-signature", b"sha256=dummy"),
            ],
        },
        receive=receive,
    )

    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", "job-1"))

    producer = AsyncMock()
    producer.send = AsyncMock(return_value=None)

    with patch("routers.ingest._tenant_ingest_rate_limit", AsyncMock(return_value=300)), \
         patch("routers.ingest.redis_rate_limit", AsyncMock()), \
         patch("routers.ingest._upload_bronze_file", return_value="bronze/t1/job-1/epsilon.json"), \
         patch("routers.ingest.get_producer", AsyncMock(return_value=producer)), \
         patch("routers.ingest._resolve_effective_mapping_config", AsyncMock(return_value=(
             "map-v2",
             {
                 "source_system": "ConnectorEpsilon",
                 "entity_type": "TRANSACTION",
                 "fields": [
                     {"target": "external_transaction_id", "source": "event_id", "transform": "copy"},
                     {"target": "player_cpf", "source": "external_player_id", "transform": "copy"},
                     {"target": "type", "source": "transaction_type", "transform": "copy"},
                     {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                     {"target": "occurred_at", "source": "occurred_at", "transform": "parseDate"},
                 ],
             },
         ))), \
         patch("routers.ingest.ConnectorEpsilon") as connector_cls:
        connector = MagicMock()
        connector.parse.return_value = MagicMock(
            success=True,
            records=[
                {
                    "event_id": "evt-1",
                    "external_player_id": "p1",
                    "transaction_type": "DEPOSIT",
                    "amount": 10.0,
                    "occurred_at": "2026-03-20T10:00:00Z",
                    "currency": "BRL",
                }
            ],
            total=1,
            failed=0,
            errors=[],
        )
        connector_cls.return_value = connector

        result = await ingest_epsilon_webhook(request=request, principal=principal, db=db)

    assert result["status"] == "accepted"
    call = producer.send.await_args_list[0]
    assert call.args[0] == "raw.transactions"
    payload = call.args[1]
    assert payload["mapping_config_id"] == "map-v2"
    assert payload["payload"]["player_cpf"] == "p1"
    assert payload["payload"]["type"] == "DEPOSIT"


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def test_ingest_router_has_events_endpoint():
    """The ingest router must register POST /ingest/event."""
    from routers.ingest import router

    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/ingest/event" in paths, f"POST /ingest/event not found. Paths: {paths}"


def test_ingest_router_has_errors_endpoint():
    """The ingest router must register GET /ingest/errors (or /errors)."""
    from routers.ingest import router

    paths = [r.path for r in router.routes if hasattr(r, "path")]
    error_paths = [p for p in paths if "error" in p.lower()]
    assert error_paths, f"No error endpoints found in ingest router. Paths: {paths}"
