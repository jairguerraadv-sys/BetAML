from __future__ import annotations

import importlib.util
import os
import sys

import pytest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SP_PATH = os.path.join(_ROOT, "services", "stream_processor", "main.py")
sys.path.insert(0, os.path.join(_ROOT, "services", "stream_processor"))
sys.path.insert(0, os.path.join(_ROOT, "libs"))

_spec = importlib.util.spec_from_file_location("stream_processor_main_dlq", _SP_PATH)
_sp_mod = importlib.util.module_from_spec(_spec)
sys.modules["stream_processor_main_dlq"] = _sp_mod
assert _spec.loader is not None
_spec.loader.exec_module(_sp_mod)


class _FakeProducer:
    def __init__(self):
        self.calls: list[tuple[str, dict, str | None]] = []

    async def send(self, topic: str, value: dict, key: str | None = None):
        self.calls.append((topic, value, key))


class _FakeMsg:
    topic = "canonical.transactions"
    partition = 2
    offset = 33


class _FakeRedis:
    def __init__(self):
        self._keys: set[str] = set()

    def dedup_key(self, tenant_id: str, source_system: str, source_event_id: str) -> str:
        return f"betaml:{tenant_id}:{source_system}:{source_event_id}"

    async def set_if_absent(self, key: str, value: str, ttl: int) -> bool:
        _ = value, ttl
        if key in self._keys:
            return False
        self._keys.add(key)
        return True


@pytest.mark.asyncio
async def test_publish_to_dlq_includes_operational_metadata():
    producer = _FakeProducer()
    msg = _FakeMsg()
    payload = {
        "tenant_id": "tenant-1",
        "event_id": "evt-1",
        "source_event_id": "src-1",
        "correlation_id": "corr-1",
        "payload": {"cpf": "12345678900", "amount": 100.0},
    }

    ok = await _sp_mod._publish_to_dlq(
        producer=producer,
        topic="canonical.transactions",
        msg=msg,
        message=payload,
        error=ValueError("invalid payload cpf=123.456.789-00"),
        retry_count=3,
    )

    assert ok is True
    assert len(producer.calls) == 1
    topic, value, key = producer.calls[0]
    assert topic.endswith(".dlq")
    assert value["payload"]["original_topic"] == "canonical.transactions"
    assert value["payload"]["original_partition"] == 2
    assert value["payload"]["original_offset"] == 33
    assert value["payload"]["error_type"] == "validation_error"
    assert "REDACTED" in value["payload"]["error_message"]
    assert key == "src-1"


@pytest.mark.asyncio
async def test_claim_event_for_processing_is_idempotent():
    redis_client = _FakeRedis()
    msg = {
        "tenant_id": "tenant-1",
        "source_system": "BackofficeAlpha",
        "source_event_id": "src-1",
        "payload": {"amount": 10},
    }

    first = await _sp_mod._claim_event_for_processing(redis_client, msg, "canonical.transactions")
    second = await _sp_mod._claim_event_for_processing(redis_client, msg, "canonical.transactions")

    assert first is True
    assert second is False


def test_validate_event_envelope_missing_fields():
    errors = _sp_mod._validate_event_envelope({"payload": []})
    assert "missing tenant_id" in errors
    assert "missing event_id/source_event_id" in errors
    assert "missing or invalid payload" in errors
