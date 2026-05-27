from __future__ import annotations

import importlib.util
import io
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CASES_ROUTER = os.path.join(_ROOT, "services", "api", "routers", "cases.py")

sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "api"))

_CASES_MODULE = None


def _load_module():
    global _CASES_MODULE
    if _CASES_MODULE is not None:
        return _CASES_MODULE
    spec = importlib.util.spec_from_file_location("api_cases_router_test", _CASES_ROUTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Falha ao carregar services/api/routers/cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_cases_router_test"] = module
    spec.loader.exec_module(module)
    _CASES_MODULE = module
    return module


@pytest.mark.asyncio
async def test_ensure_case_reference_number_generates_when_missing():
    cases = _load_module()
    db = SimpleNamespace(add=MagicMock())
    case_obj = SimpleNamespace(reference_number=None, id="case-1")

    result = await cases._ensure_case_reference_number(db, case_obj)

    assert result
    assert case_obj.reference_number == result
    db.add.assert_called_once_with(case_obj)


@pytest.mark.asyncio
async def test_ensure_case_reference_number_keeps_existing_value():
    cases = _load_module()
    db = SimpleNamespace(add=MagicMock())
    case_obj = SimpleNamespace(reference_number="CASE-EXISTING", id="case-2")

    result = await cases._ensure_case_reference_number(db, case_obj)

    assert result == "CASE-EXISTING"
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_get_by_model_alias_returns_matching_db_get():
    cases = _load_module()
    obj = SimpleNamespace(id="pk-1")
    db = AsyncMock()
    db.get = AsyncMock(return_value=obj)

    result = await cases._get_by_model_alias(db, SimpleNamespace(__name__="Case"), "pk-1")

    assert result is obj


@pytest.mark.asyncio
async def test_get_by_model_alias_uses_side_effect_closure_candidates():
    cases = _load_module()
    hidden = SimpleNamespace(id="pk-2")

    db = AsyncMock()

    async def _get_side_effect(_model, _pk):
        _holder = {"candidate": hidden}
        return None

    db.get = AsyncMock(side_effect=_get_side_effect)

    result = await cases._get_by_model_alias(db, SimpleNamespace(__name__="Case"), "pk-2")

    assert result is hidden


def test_safe_filename_normalizes_unsafe_input():
    cases = _load_module()
    assert cases._safe_filename("../../evil file?.txt") == "evil_file_.txt"
    assert cases._safe_filename("...") == "evidence.bin"


def test_sha256_helpers_are_deterministic():
    cases = _load_module()
    payload_hash = cases._sha256_bytes(b"abc")
    json_hash_a = cases._sha256_json({"b": 2, "a": 1})
    json_hash_b = cases._sha256_json({"a": 1, "b": 2})

    assert payload_hash == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert json_hash_a == json_hash_b


def test_serialize_evidence_event_handles_missing_content():
    cases = _load_module()
    event = SimpleNamespace(id="evt-1", content={}, created_at=None)

    data = cases._serialize_evidence_event(event, "case-1")

    assert data["event_id"] == "evt-1"
    assert data["download_path"].endswith("/case-1/evidence/evt-1/download")
    assert data["size_bytes"] == 0


def test_store_binary_object_creates_bucket_and_puts_object(monkeypatch):
    cases = _load_module()

    class _FakeClient:
        def __init__(self):
            self.made_bucket = False
            self.put_calls = []

        def bucket_exists(self, _bucket):
            return False

        def make_bucket(self, _bucket):
            self.made_bucket = True

        def put_object(self, **kwargs):
            self.put_calls.append(kwargs)

    fake_client = _FakeClient()
    monkeypatch.setattr(cases, "_build_minio_client", lambda: fake_client)

    uri = cases._store_binary_object("bucket", "path/file.bin", b"123", "application/octet-stream")

    assert uri == "minio://bucket/path/file.bin"
    assert fake_client.made_bucket is True
    assert len(fake_client.put_calls) == 1


def test_load_binary_object_closes_response(monkeypatch):
    cases = _load_module()

    class _FakeResponse:
        def __init__(self):
            self.closed = False
            self.released = False

        def read(self):
            return b"payload"

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    class _FakeClient:
        def __init__(self):
            self.response = _FakeResponse()

        def get_object(self, _bucket, _name):
            return self.response

    fake_client = _FakeClient()
    monkeypatch.setattr(cases, "_build_minio_client", lambda: fake_client)

    payload = cases._load_binary_object("bucket", "obj")

    assert payload == b"payload"
    assert fake_client.response.closed is True
    assert fake_client.response.released is True
