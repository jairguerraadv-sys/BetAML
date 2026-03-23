from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

import structlog
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_extract_request_and_event_id_from_kafka_headers():
    from libs.clients import _event_id_from_kafka_headers, _request_id_from_kafka_headers

    headers = [
        ("X-Request-ID", b"req-123"),
        ("X-Event-ID", b"evt-999"),
    ]

    assert _request_id_from_kafka_headers(headers) == "req-123"
    assert _event_id_from_kafka_headers(headers) == "evt-999"


@pytest.mark.asyncio
async def test_kafka_producer_auto_injects_trace_headers():
    from libs.clients import KafkaProducerClient

    client = KafkaProducerClient("kafka:9092")
    client._producer = AsyncMock()

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-abc")

    await client.send("topic.events", {"event_id": "evt-123", "payload": {"x": 1}}, key="evt-123")

    kwargs = client._producer.send_and_wait.await_args.kwargs
    headers = dict(kwargs["headers"])
    assert headers["X-Request-ID"] == b"req-abc"
    assert headers["X-Event-ID"] == b"evt-123"
