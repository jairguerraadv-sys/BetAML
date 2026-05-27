from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SP_PATH = os.path.join(_ROOT, "services", "stream_processor", "main.py")
sys.path.insert(0, os.path.join(_ROOT, "services", "stream_processor"))
sys.path.insert(0, os.path.join(_ROOT, "libs"))

_spec = importlib.util.spec_from_file_location("stream_processor_main_ingest_jobs", _SP_PATH)
_sp_mod = importlib.util.module_from_spec(_spec)
sys.modules["stream_processor_main_ingest_jobs"] = _sp_mod
assert _spec.loader is not None
_spec.loader.exec_module(_sp_mod)


class _FakeObject:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


def _minio_factory(payload: bytes):
    class _FakeMinio:
        def __init__(self, *args, **kwargs):
            pass

        def get_object(self, bucket: str, path: str) -> _FakeObject:
            _ = bucket, path
            return _FakeObject(payload)

    return _FakeMinio


def _msg(*, source_system: str, file_name: str) -> dict[str, str]:
    return {
        "job_id": "job-1",
        "tenant_id": "tenant-1",
        "source_system": source_system,
        "file_name": file_name,
        "file_path": "bronze/tenant-1/ingest_jobs/job-1/file",
        "mapping_config_id": None,
    }


@pytest.mark.asyncio
async def test_process_ingest_job_connector_gamma_uses_native_parser_with_partial_result():
    xml_payload = b"""<Events>
  <Transaction>
    <EventId>G-OK-1</EventId>
    <PlayerId>PLY-1</PlayerId>
    <Type>DEPOSIT</Type>
    <Amount currency=\"BRL\">100.00</Amount>
    <Timestamp>2026-03-21T10:00:00Z</Timestamp>
  </Transaction>
  <Transaction>
    <EventId>G-BAD-1</EventId>
    <PlayerId>PLY-2</PlayerId>
    <Type>DEPOSIT</Type>
    <Amount currency=\"BRL\">-10.00</Amount>
    <Timestamp>2026-03-21T10:05:00Z</Timestamp>
  </Transaction>
</Events>"""

    updates: list[tuple[tuple, dict]] = []
    ingest_errors: list[dict] = []

    async def _fake_to_thread(func, /, *args, **kwargs):
        name = getattr(func, "__name__", "")
        if name == "_update_job":
            updates.append((args, kwargs))
            return None
        if name == "_insert_ingest_error":
            ingest_errors.append(kwargs)
            return None
        return func(*args, **kwargs)

    producer = MagicMock()
    producer.send = AsyncMock(return_value=None)

    def _validate(_entity_type, payload):
        amount = payload.get("amount")
        if isinstance(amount, (int, float)) and amount < 0:
            return {"valid": False, "validation_errors": ["amount must be >= 0"]}
        return {"valid": True, "validation_errors": []}

    with patch.dict(os.environ, {"MINIO_SECRET_KEY": "test-secret"}, clear=False), \
         patch("minio.Minio", _minio_factory(xml_payload)), patch.object(
        _sp_mod.asyncio, "to_thread", side_effect=_fake_to_thread
    ), patch("libs.mapping.validate_canonical_ingest_payload", side_effect=_validate):
        await _sp_mod.process_ingest_job(_msg(source_system="ConnectorGamma", file_name="gamma.xml"), MagicMock(), MagicMock(), producer)

    topics = [call.args[0] for call in producer.send.await_args_list]
    assert topics == ["canonical.transactions", "canonical.transactions"]

    final_args, final_kwargs = updates[-1]
    assert final_args[0] == "DONE"
    assert final_args[1] == 2
    assert final_args[2] == 2
    assert final_args[3] == 0
    assert isinstance(final_kwargs.get("error_sample"), list)
    assert len(ingest_errors) == 0


@pytest.mark.asyncio
async def test_process_ingest_job_connector_delta_uses_native_parser_with_line_errors():
    ndjson_payload = b"\n".join(
        [
            b'{"id":"D-OK-1","uid":"PLY-1","evt_type":"DEPOSIT","ts":"2026-03-21T11:00:00Z","val":120.0,"ccy":"BRL"}',
            b'{"id":"D-BAD-MALFORMED","uid":"PLY-2","evt_type":"DEPOSIT","ts":"2026-03-21T11:05:00Z","val":90.0',
            b'{"id":"D-BAD-NEG","uid":"PLY-3","evt_type":"DEPOSIT","ts":"2026-03-21T11:10:00Z","val":-5.0,"ccy":"BRL"}',
        ]
    )

    updates: list[tuple[tuple, dict]] = []
    ingest_errors: list[dict] = []

    async def _fake_to_thread(func, /, *args, **kwargs):
        name = getattr(func, "__name__", "")
        if name == "_update_job":
            updates.append((args, kwargs))
            return None
        if name == "_insert_ingest_error":
            ingest_errors.append(kwargs)
            return None
        return func(*args, **kwargs)

    producer = MagicMock()
    producer.send = AsyncMock(return_value=None)

    def _validate(_entity_type, payload):
        amount = payload.get("amount")
        if isinstance(amount, (int, float)) and amount < 0:
            return {"valid": False, "validation_errors": ["amount must be >= 0"]}
        return {"valid": True, "validation_errors": []}

    with patch.dict(os.environ, {"MINIO_SECRET_KEY": "test-secret"}, clear=False), \
         patch("minio.Minio", _minio_factory(ndjson_payload)), patch.object(
        _sp_mod.asyncio, "to_thread", side_effect=_fake_to_thread
    ), patch("libs.mapping.validate_canonical_ingest_payload", side_effect=_validate):
        await _sp_mod.process_ingest_job(_msg(source_system="ConnectorDelta", file_name="delta.ndjson"), MagicMock(), MagicMock(), producer)

    topics = [call.args[0] for call in producer.send.await_args_list]
    assert topics == ["canonical.transactions", "canonical.transactions"]

    final_args, final_kwargs = updates[-1]
    assert final_args[0] == "PARTIAL"
    assert final_args[1] == 3
    assert final_args[2] == 2
    assert final_args[3] == 1
    assert isinstance(final_kwargs.get("error_sample"), list)
    assert len(ingest_errors) == 1


@pytest.mark.asyncio
async def test_process_ingest_job_connector_uses_mapping_before_publish():
    xml_payload = b"""<Events>
  <Transaction>
    <EventId>G-MAP-1</EventId>
    <PlayerId>PLY-MAP-1</PlayerId>
    <Type>DEPOSIT</Type>
    <Amount currency=\"BRL\">100.00</Amount>
    <Timestamp>2026-03-21T10:00:00Z</Timestamp>
  </Transaction>
</Events>"""

    async def _fake_to_thread(func, /, *args, **kwargs):
        return None

    producer = MagicMock()
    producer.send = AsyncMock(return_value=None)

    with patch.dict(os.environ, {"MINIO_SECRET_KEY": "test-secret"}, clear=False), \
         patch("minio.Minio", _minio_factory(xml_payload)), patch.object(
        _sp_mod.asyncio, "to_thread", side_effect=_fake_to_thread
    ), patch("libs.mapping.validate_canonical_ingest_payload", return_value={"valid": True, "validation_errors": []}):
        await _sp_mod.process_ingest_job(_msg(source_system="ConnectorGamma", file_name="gamma.xml"), MagicMock(), MagicMock(), producer)

    payload = producer.send.await_args_list[0].args[1]["payload"]
    assert payload["player_cpf"] == "PLY-MAP-1"
    assert payload["type"] == "DEPOSIT"
    assert "external_player_id" not in payload


@pytest.mark.asyncio
async def test_process_ingest_job_derives_dlq_topic_from_failed_entity_type():
    ndjson_payload = b'{"entity_type":"BET","player_id":"PLY-1","stake_amount":15,"placed_at":"2026-03-21T11:00:00Z"}\n'

    async def _fake_to_thread(func, /, *args, **kwargs):
        return None

    producer = MagicMock()

    async def _send(topic, payload, key=None):
        if topic == "canonical.bets.dlq":
            return None
        raise RuntimeError("publish boom")

    producer.send = AsyncMock(side_effect=_send)

    with patch.dict(os.environ, {"MINIO_SECRET_KEY": "test-secret"}, clear=False), \
         patch("minio.Minio", _minio_factory(ndjson_payload)), \
         patch.object(_sp_mod.asyncio, "to_thread", side_effect=_fake_to_thread), \
         patch("libs.mapping.validate_canonical_ingest_payload", return_value={"valid": True, "validation_errors": []}):
        await _sp_mod.process_ingest_job(_msg(source_system="BackofficeAlpha", file_name="bets.ndjson"), MagicMock(), MagicMock(), producer)

    topics = [call.args[0] for call in producer.send.await_args_list]
    assert "canonical.bets.dlq" in topics
    assert "canonical.transactions" in topics
