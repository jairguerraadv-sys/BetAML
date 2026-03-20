"""
E2E tests for Stream Processor (Task 11).

Requires Docker stack running (infra/docker-compose.yml).
By default these tests are skipped. To run:

  TEST_STACK_UP=1 pytest tests/integration/test_stream_processor_e2e.py -v

Assumptions:
- Redpanda is reachable at localhost:9092 (default docker-compose port mapping)
- stream-processor container is running and consuming topics
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
import requests

RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"
BASE_URL = os.getenv("API_URL", "http://localhost:8000")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack não disponível. Use TEST_STACK_UP=1 para rodar testes de integração.",
)


def api(path: str, method: str = "GET", **kwargs) -> requests.Response:
    return requests.request(method, f"{BASE_URL}{path}", timeout=15, **kwargs)


def _login(username: str, password: str) -> dict:
    resp = api("/auth/login", "POST", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login falhou ({username}): {resp.text}"
    return resp.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _await_canonical_transaction(source_event_id: str, timeout_s: float = 15.0) -> dict:
    """Consume canonical.transactions until matching source_event_id appears."""
    from aiokafka import AIOKafkaConsumer

    group_id = f"pytest-e2e-{uuid.uuid4().hex[:10]}"
    consumer = AIOKafkaConsumer(
        "canonical.transactions",
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )
    await consumer.start()
    try:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            batch = await consumer.getmany(timeout_ms=500, max_records=50)
            for _, messages in batch.items():
                for msg in messages:
                    value = msg.value
                    if not isinstance(value, dict):
                        continue
                    if value.get("source_event_id") == source_event_id:
                        return value
        raise AssertionError(f"Timeout aguardando canonical.transactions source_event_id={source_event_id}")
    finally:
        await consumer.stop()


@skip_unless_stack
@pytest.mark.asyncio
async def test_raw_transaction_produces_canonical_transaction():
    """Publishes raw.transactions and expects a canonical.transactions output."""
    token = _login("admin_a", "admin123")["access_token"]
    me = api("/me", headers=_headers(token))
    assert me.status_code == 200
    tenant_id = me.json()["tenant_id"]

    player_id = f"PLY-{uuid.uuid4().hex[:8]}"
    source_event_id = str(uuid.uuid4())

    raw_event = {
        "event_id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "source_system": "BackofficeAlpha",
        "source_event_id": source_event_id,
        "payload": {
            "player_id": player_id,
            "amount": 1500.0,
            "currency": "BRL",
            "transaction_type": "DEPOSIT",
            "occurred_at": "2026-03-20T10:00:00Z",
            "method": "PIX",
            "status": "SETTLED",
        },
    }

    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda v: v.encode("utf-8"),
    )
    await producer.start()
    try:
        await producer.send_and_wait("raw.transactions", raw_event, key=source_event_id)
    finally:
        await producer.stop()

    canonical = await _await_canonical_transaction(source_event_id)
    assert canonical["tenant_id"] == tenant_id
    assert canonical["source_system"] == "BackofficeAlpha"
    assert canonical["entity_type"] == "TRANSACTION"
    assert canonical["payload"]["player_id"] == player_id
    assert canonical["payload"]["type"] == "DEPOSIT"
    assert float(canonical["payload"]["amount"]) == 1500.0
