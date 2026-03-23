"""
tests/unit/test_ingest_extended.py — Extended unit tests for routers/ingest.py

Tests cover:
  - _build_envelope: uuid event_id, required fields, ingest_metadata structure
  - _publish_with_retries: success on first send, DLQ on max retries
  - _tenant_ingest_rate_limit: returns default when no flag, reads flag_value
  - ingest_batch: mixed valid/invalid source systems, no-producer path
  - WebsocketIngestRequest / ReplayIngestErrorRequest schema
  - Router path registration (/ingest/batch, /ingest/event, /ingest/errors)
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_db(scalar_one_result=None):
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=None)

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = scalar_one_result
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    return db


class _FakeUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


# ---------------------------------------------------------------------------
# _build_envelope
# ---------------------------------------------------------------------------

def test_build_envelope_has_event_id():
    from routers.ingest import _build_envelope
    import uuid
    env = _build_envelope(
        tenant_id="t1",
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={"amount": 100},
        source_event_id="se-1",
    )
    # Must be a valid UUID
    uuid.UUID(env["event_id"])


def test_build_envelope_required_fields():
    from routers.ingest import _build_envelope
    env = _build_envelope(
        tenant_id="t1",
        source_system="SportsBook",
        entity_type="BET",
        payload={"bet_id": "B-1"},
        source_event_id="se-2",
    )
    assert env["tenant_id"] == "t1"
    assert env["source_system"] == "SportsBook"
    assert env["entity_type"] == "BET"
    assert env["payload"] == {"bet_id": "B-1"}
    assert env["source_event_id"] == "se-2"
    assert env["schema_version"] == 1


def test_build_envelope_ingest_metadata_keys():
    from routers.ingest import _build_envelope
    env = _build_envelope(
        tenant_id="t1",
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={},
        source_event_id="se-3",
    )
    meta = env["ingest_metadata"]
    assert "received_at" in meta
    assert "mapper_version" in meta
    assert meta["mapper_version"] == "1.0"


def test_build_envelope_with_extra_metadata():
    from routers.ingest import _build_envelope
    env = _build_envelope(
        tenant_id="t1",
        source_system="CasinoEngine",
        entity_type="TRANSACTION",
        payload={},
        source_event_id="se-4",
        ingest_metadata={"connector": "gamma", "batch_id": "b-99"},
    )
    assert env["ingest_metadata"]["connector"] == "gamma"
    assert env["ingest_metadata"]["batch_id"] == "b-99"


def test_build_envelope_mapping_config_id_none_by_default():
    from routers.ingest import _build_envelope
    env = _build_envelope(
        tenant_id="t1",
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={},
        source_event_id="se-5",
    )
    assert env["mapping_config_id"] is None


# ---------------------------------------------------------------------------
# _publish_with_retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_with_retries_success_on_first_send():
    from routers.ingest import _publish_with_retries

    producer = MagicMock()
    producer.send = AsyncMock(return_value=None)

    result = await _publish_with_retries(
        producer=producer,
        topic="raw.transactions",
        payload={"amount": 100},
        key="k-1",
        tenant_id="t1",
        source_system="BackofficeAlpha",
    )

    assert result is True
    producer.send.assert_called_once()


@pytest.mark.asyncio
async def test_publish_with_retries_sends_to_dlq_on_all_failures():
    from routers.ingest import _publish_with_retries

    # Every send raises — main topic + DLQ attempt all fail
    producer = MagicMock()
    producer.send = AsyncMock(side_effect=Exception("broker unavailable"))

    result = await _publish_with_retries(
        producer=producer,
        topic="raw.transactions",
        payload={"amount": 100},
        key="k-dlq",
        tenant_id="t1",
        source_system="BackofficeAlpha",
    )

    assert result is False
    # Should have attempted the DLQ topic as well
    calls = [str(c) for c in producer.send.call_args_list]
    dlq_calls = [c for c in calls if "dlq" in c]
    assert len(dlq_calls) > 0


# ---------------------------------------------------------------------------
# _tenant_ingest_rate_limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_rate_limit_returns_default_when_no_flag():
    from routers.ingest import _tenant_ingest_rate_limit

    db = _make_db(scalar_one_result=None)
    result = await _tenant_ingest_rate_limit(db, "t1", default_limit=300)

    assert result == 300


@pytest.mark.asyncio
async def test_tenant_rate_limit_reads_flag_value():
    from routers.ingest import _tenant_ingest_rate_limit

    flag = MagicMock()
    flag.flag_value = "150"
    flag.value = "150"
    db = _make_db(scalar_one_result=flag)

    result = await _tenant_ingest_rate_limit(db, "t1", default_limit=300)

    assert result == 150


# ---------------------------------------------------------------------------
# ingest_batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_batch_rejects_unknown_source_system():
    from routers.ingest import ingest_batch, IngestEventRequest

    events = [
        IngestEventRequest(
            source_system="UnknownSystem",
            entity_type="TRANSACTION",
            payload={"amount": 100},
        )
    ]
    db = _make_db()
    user = _make_user()

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None):
        result = await ingest_batch(events=events, current_user=user, db=db)

    assert result["count"] == 1
    assert result["results"][0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_ingest_batch_queues_valid_event_without_producer():
    from routers.ingest import ingest_batch, IngestEventRequest

    events = [
        IngestEventRequest(
            source_system="BackofficeAlpha",
            entity_type="TRANSACTION",
            payload={"amount": 500, "player_id": "P-1"},
        )
    ]
    db = _make_db()
    user = _make_user()

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None):
        result = await ingest_batch(events=events, current_user=user, db=db)

    assert result["count"] == 1
    assert result["results"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_ingest_batch_mixed_events():
    """Valid events are queued; invalid source_systems are rejected."""
    from routers.ingest import ingest_batch, IngestEventRequest

    events = [
        IngestEventRequest(source_system="BackofficeAlpha", entity_type="TRANSACTION", payload={}),
        IngestEventRequest(source_system="Ghost", entity_type="BET", payload={}),
        IngestEventRequest(source_system="SportsBook", entity_type="BET", payload={}),
    ]
    db = _make_db()
    user = _make_user()

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None):
        result = await ingest_batch(events=events, current_user=user, db=db)

    statuses = [r["status"] for r in result["results"]]
    assert statuses.count("queued") == 2
    assert statuses.count("rejected") == 1


@pytest.mark.asyncio
async def test_ingest_batch_with_producer_enqueues_and_returns_event_ids():
    from routers.ingest import ingest_batch, IngestEventRequest

    events = [
        IngestEventRequest(source_system="ConnectorGamma", entity_type="TRANSACTION", payload={}),
    ]
    db = _make_db()
    user = _make_user()

    mock_producer = AsyncMock()
    mock_producer.send = AsyncMock(return_value=None)

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=mock_producer):
        result = await ingest_batch(events=events, current_user=user, db=db)

    assert result["results"][0]["status"] == "queued"
    assert "event_id" in result["results"][0]


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_websocket_ingest_request_schema():
    from routers.ingest import WebsocketIngestRequest
    req = WebsocketIngestRequest(
        source_system="BackofficeAlpha",
        entity_type="BET",
        payload={"bet_id": "B-1"},
        source_event_id="ext-999",
    )
    assert req.source_system == "BackofficeAlpha"
    assert req.source_event_id == "ext-999"


def test_replay_ingest_error_request_defaults():
    from routers.ingest import ReplayIngestErrorRequest
    req = ReplayIngestErrorRequest(corrected_payload={"amount": 100})
    assert req.resolve_original is True
    assert req.note is None
    assert req.entity_type is None


def test_resolve_ingest_error_request_note_optional():
    from routers.ingest import ResolveIngestErrorRequest
    req = ResolveIngestErrorRequest()
    assert req.note is None


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def test_ingest_router_has_event_endpoint():
    from routers.ingest import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/ingest/event" in paths


def test_ingest_router_has_batch_endpoint():
    from routers.ingest import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/ingest/batch" in paths


def test_ingest_router_has_errors_endpoint():
    from routers.ingest import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    error_paths = [p for p in paths if "error" in p.lower()]
    assert error_paths, f"No error endpoints found. Paths: {paths}"


def test_ingest_router_has_streaming_endpoints():
    from routers.ingest import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/ingest/stream" in paths
    assert "/ingest/ws" in paths


@pytest.mark.asyncio
async def test_ingest_sse_stream_returns_heartbeat_chunk():
    from routers.ingest import ingest_sse_stream

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/ingest/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 1234),
        },
        receive=receive,
    )

    with patch.object(request, "is_disconnected", AsyncMock(side_effect=[False, True])), \
         patch("routers.ingest.asyncio.sleep", AsyncMock(return_value=None)):
        response = await ingest_sse_stream(request=request, current_user=_make_user())
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

    body = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in chunks).decode("utf-8")
    assert "heartbeat" in body
    assert "data:" in body


@pytest.mark.asyncio
async def test_ingest_file_persists_bronze_path_without_refresh_after_second_commit():
    from routers.ingest import ingest_file, IngestPrincipal

    db = _make_db()
    db.refresh = AsyncMock()
    principal = IngestPrincipal(tenant_id="t1", id="u1", role="ADMIN")
    upload = _FakeUploadFile(
        "transactions.csv",
        b"player_id,amount,currency,transaction_type,occurred_at,method,status\nPLY-1,100,BRL,DEPOSIT,2026-03-20T10:00:00Z,PIX,SETTLED\n",
    )

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None), \
         patch("routers.ingest._upload_bronze_file", return_value="bronze/t1/ingest_jobs/job-1/transactions.csv"):
        response = await ingest_file(
            background_tasks=MagicMock(),
            file=upload,
            source_system="BackofficeAlpha",
            mapping_config_id=None,
            principal=principal,
            db=db,
        )

    assert response["status"] == "QUEUED"
    assert db.refresh.await_count == 1


@pytest.mark.asyncio
async def test_ingest_file_keeps_flow_when_refresh_fails():
    from routers.ingest import ingest_file, IngestPrincipal

    db = _make_db()
    db.refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
    principal = IngestPrincipal(tenant_id="t1", id="u1", role="ADMIN")
    upload = _FakeUploadFile(
        "transactions.csv",
        b"player_id,amount,currency,transaction_type,occurred_at,method,status\nPLY-1,100,BRL,DEPOSIT,2026-03-20T10:00:00Z,PIX,SETTLED\n",
    )

    with patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None), \
         patch("routers.ingest._upload_bronze_file", return_value="bronze/t1/ingest_jobs/job-1/transactions.csv"):
        response = await ingest_file(
            background_tasks=MagicMock(),
            file=upload,
            source_system="BackofficeAlpha",
            mapping_config_id=None,
            principal=principal,
            db=db,
        )

    assert response["status"] == "QUEUED"
    assert db.refresh.await_count == 1
    assert db.commit.await_count >= 1


@pytest.mark.asyncio
async def test_parse_connector_payload_keeps_flow_when_refresh_fails():
    from routers.ingest import parse_connector_payload

    db = _make_db()
    db.refresh = AsyncMock(side_effect=RuntimeError("refresh failed"))
    current_user = _make_user(role="ADMIN")
    upload = _FakeUploadFile(
        "gamma.xml",
        b"""<Events><Transaction><EventId>g-1</EventId><PlayerId>P-1</PlayerId><Type>DEPOSIT</Type><Amount currency='BRL'>100.0</Amount><Timestamp>2026-03-20T10:00:00Z</Timestamp><Instrument><Type>PIX</Type><Token>pix-1</Token></Instrument><DeviceId>d-1</DeviceId></Transaction></Events>""",
    )

    parse_result = MagicMock()
    parse_result.total = 1
    parse_result.failed = 0
    parse_result.records = [{"event_id": "g-1", "external_player_id": "P-1", "transaction_type": "DEPOSIT", "amount": 100}]
    parse_result.errors = []
    connector = MagicMock()
    connector.parse.return_value = parse_result

    with patch("routers.ingest._ensure_db_tenant_context", new_callable=AsyncMock), \
         patch("routers.ingest._tenant_ingest_rate_limit", new_callable=AsyncMock, return_value=300), \
         patch("routers.ingest.redis_rate_limit", new_callable=AsyncMock), \
         patch("routers.ingest.get_connector", return_value=connector), \
         patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=None), \
         patch("routers.ingest._upload_bronze_file", return_value="bronze/t1/ingest_jobs/job-1/gamma.xml"):
        response = await parse_connector_payload(
            connector_name="gamma",
            file=upload,
            entity_type="TRANSACTION",
            current_user=current_user,
            db=db,
        )

    assert response["status"] == "DONE"
    assert response["summary"]["accepted"] == 1
    assert response["summary"]["failed"] == 0
    assert db.refresh.await_count == 1


@pytest.mark.asyncio
async def test_reprocess_job_queues_without_refresh_after_commit():
    from routers.ingest import reprocess_job, ReprocessRequest

    db = _make_db()
    db.refresh = AsyncMock()
    current_user = _make_user(role="ADMIN")
    original_job = MagicMock()
    original_job.id = "job-original"
    original_job.tenant_id = "t1"
    original_job.status = "DONE"
    original_job.file_path = "bronze/t1/ingest_jobs/job-original/file.csv"
    original_job.mapping_config_id = None
    original_job.source_system = "BackofficeAlpha"
    original_job.connector_type = "FILE"
    original_job.file_name = "file.csv"
    original_job.file_size_bytes = 128
    db.get = AsyncMock(return_value=original_job)

    producer = MagicMock()
    producer.send = AsyncMock(return_value=None)

    with patch("routers.ingest.get_producer", new_callable=AsyncMock, return_value=producer):
        response = await reprocess_job(
            job_id="job-original",
            body=ReprocessRequest(reason="retry_e2e"),
            current_user=current_user,
            db=db,
        )

    assert response["status"] == "QUEUED"
    assert db.refresh.await_count == 0
