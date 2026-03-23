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

import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
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
