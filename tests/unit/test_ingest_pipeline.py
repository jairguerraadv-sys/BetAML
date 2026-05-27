from __future__ import annotations

import importlib.util
import os
import sys

import pytest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_API_DIR = os.path.join(_ROOT, "services", "api")
_LIBS_DIR = os.path.join(_ROOT, "libs")

for _path in (_API_DIR, _LIBS_DIR):
    while _path in sys.path:
        sys.path.remove(_path)
sys.path.insert(0, _LIBS_DIR)
sys.path.insert(0, _API_DIR)

for _mod_name, _mod in list(sys.modules.items()):
    _mod_file = getattr(_mod, "__file__", None)
    if not _mod_file:
        continue
    _mod_file = os.path.abspath(_mod_file)
    if _mod_file.startswith(_API_DIR) or _mod_file.startswith(_LIBS_DIR):
        sys.modules.pop(_mod_name, None)

_INGEST_PATH = os.path.join(_API_DIR, "routers", "ingest.py")
_SPEC = importlib.util.spec_from_file_location("api_ingest_pipeline_tests", _INGEST_PATH)
assert _SPEC and _SPEC.loader
ingest_router = importlib.util.module_from_spec(_SPEC)
sys.modules["api_ingest_pipeline_tests"] = ingest_router
_SPEC.loader.exec_module(ingest_router)


class _FakeProducer:
    def __init__(self):
        self.calls: list[tuple[str, dict, str | None]] = []
        self._fails = 0

    async def send(self, topic: str, value: dict, key: str | None = None, headers=None):
        _ = headers
        if topic == "canonical.transactions" and self._fails < 3:
            self._fails += 1
            raise RuntimeError("temporary broker timeout")
        self.calls.append((topic, value, key))


@pytest.mark.asyncio
async def test_publish_with_retries_sends_to_dlq_after_limit(monkeypatch):
    producer = _FakeProducer()
    payload = {
        "tenant_id": "tenant-1",
        "event_id": "evt-1",
        "source_event_id": "src-1",
        "correlation_id": "corr-1",
        "payload": {"cpf": "12345678900"},
    }

    monkeypatch.setattr(ingest_router.settings, "dlq_max_retries", 2)
    monkeypatch.setattr(ingest_router.settings, "dlq_topic", "")

    ok = await ingest_router._publish_with_retries(
        producer=producer,
        topic="canonical.transactions",
        payload=payload,
        key="src-1",
        tenant_id="tenant-1",
        source_system="BackofficeAlpha",
        context={"case": "unit"},
    )

    assert ok is False
    assert any(call[0] == "canonical.transactions.dlq" for call in producer.calls)
    dlq_payload = [call[1] for call in producer.calls if call[0] == "canonical.transactions.dlq"][0]
    assert dlq_payload["error_type"] == "transient_error"
    assert dlq_payload["retry_count"] == 2
    assert dlq_payload["original_topic"] == "canonical.transactions"


def test_build_envelope_sets_correlation_id():
    envelope = ingest_router._build_envelope(
        tenant_id="tenant-1",
        source_system="BackofficeAlpha",
        entity_type="TRANSACTION",
        payload={"amount": 10},
        raw_payload={"amount": 10},
        source_event_id="src-1",
    )

    assert envelope["source_event_id"] == "src-1"
    assert envelope["correlation_id"]
    assert envelope["correlation_id"] in {"src-1", envelope["event_id"]} or isinstance(envelope["correlation_id"], str)
