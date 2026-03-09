import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

# Keep services/api before libs to avoid models circular import aliasing in mixed test runs.
_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_services_api = os.path.join(_root, "services", "api")
_libs = os.path.join(_root, "libs")
for p in (_services_api, _libs):
    while p in sys.path:
        sys.path.remove(p)
sys.path.insert(0, _libs)
sys.path.insert(0, _services_api)
for key in ("models", "libs.models"):
    sys.modules.pop(key, None)

from routers import ingest


class _FakeProducer:
    def __init__(self, fail_plan: dict[str, int], fail_dlq: bool = False):
        self.fail_plan = dict(fail_plan)
        self.fail_dlq = fail_dlq
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[tuple[str, dict]] = []

    async def send(self, topic, payload, key=None):
        self.calls.append((topic, str(key)))
        if isinstance(payload, dict):
            self.payloads.append((topic, payload))
        if topic.endswith(".dlq") and self.fail_dlq:
            raise RuntimeError("dlq down")
        remaining = self.fail_plan.get(topic, 0)
        if remaining > 0:
            self.fail_plan[topic] = remaining - 1
            raise RuntimeError(f"send fail {topic}")
        return True


@pytest.mark.asyncio
async def test_publish_with_retries_success_first_try(monkeypatch):
    producer = _FakeProducer(fail_plan={})
    monkeypatch.setattr(ingest, "settings", SimpleNamespace(dlq_max_retries=3))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    ok = await ingest._publish_with_retries(
        producer=producer,
        topic="raw.transactions",
        payload={"x": 1},
        key="k1",
        tenant_id="t1",
        source_system="ConnectorDelta",
        context={"endpoint": "/ingest/event"},
    )

    assert ok is True
    assert producer.calls == [("raw.transactions", "k1")]


@pytest.mark.asyncio
async def test_publish_with_retries_eventual_success(monkeypatch):
    producer = _FakeProducer(fail_plan={"raw.transactions": 2})
    monkeypatch.setattr(ingest, "settings", SimpleNamespace(dlq_max_retries=3))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    ok = await ingest._publish_with_retries(
        producer=producer,
        topic="raw.transactions",
        payload={"x": 1},
        key="k2",
        tenant_id="t1",
        source_system="ConnectorDelta",
        context={"endpoint": "/ingest/batch"},
    )

    assert ok is True
    assert producer.calls == [
        ("raw.transactions", "k2"),
        ("raw.transactions", "k2"),
        ("raw.transactions", "k2"),
    ]


@pytest.mark.asyncio
async def test_publish_with_retries_sends_to_dlq_after_exhaustion(monkeypatch):
    producer = _FakeProducer(fail_plan={"raw.transactions": 99})
    monkeypatch.setattr(ingest, "settings", SimpleNamespace(dlq_max_retries=3))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    ok = await ingest._publish_with_retries(
        producer=producer,
        topic="raw.transactions",
        payload={"x": 1},
        key="k3",
        tenant_id="t1",
        source_system="ConnectorGamma",
        context={"endpoint": "/ingest/connectors/gamma/parse"},
    )

    assert ok is False
    assert producer.calls == [
        ("raw.transactions", "k3"),
        ("raw.transactions", "k3"),
        ("raw.transactions", "k3"),
        ("raw.transactions.dlq", "k3"),
    ]
    dlq_payload = [p for topic, p in producer.payloads if topic == "raw.transactions.dlq"][0]
    assert dlq_payload["tenant_id"] == "t1"
    assert dlq_payload["source_system"] == "ConnectorGamma"
    assert dlq_payload["target_topic"] == "raw.transactions"
    assert dlq_payload["attempt"] == 3
    assert dlq_payload["max_retries"] == 3
    assert dlq_payload["context"]["endpoint"] == "/ingest/connectors/gamma/parse"
    assert "failed_at" in dlq_payload


@pytest.mark.asyncio
async def test_publish_with_retries_returns_false_if_dlq_also_fails(monkeypatch):
    producer = _FakeProducer(fail_plan={"ingest.jobs": 99}, fail_dlq=True)
    monkeypatch.setattr(ingest, "settings", SimpleNamespace(dlq_max_retries=2))

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    ok = await ingest._publish_with_retries(
        producer=producer,
        topic="ingest.jobs",
        payload={"job_id": "j1"},
        key="j1",
        tenant_id="t1",
        source_system="ConnectorGamma",
        context={"endpoint": "/ingest/file"},
    )

    assert ok is False
    assert producer.calls == [
        ("ingest.jobs", "j1"),
        ("ingest.jobs", "j1"),
        ("ingest.jobs.dlq", "j1"),
    ]
