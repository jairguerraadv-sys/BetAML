from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException


@pytest.mark.asyncio
async def test_mock_provider_force_fail(monkeypatch):
    from routers.external_validation import _mock_provider_call

    monkeypatch.setenv("EXTERNAL_VALIDATION_FORCE_FAIL", "1")
    with pytest.raises(RuntimeError):
        await _mock_provider_call("mock_identity", "CPF_IDENTITY", "req1")


@pytest.mark.asyncio
async def test_external_validation_provider_contract_exposes_runtime_configuration():
    from routers.external_validation import get_external_validation_provider_contract

    result = await get_external_validation_provider_contract(current_user=_make_user(role="ADMIN"))

    assert result.configured_provider in {"mock_identity", "mock"}
    assert result.environment in {"development", "test", "staging", "production"}
    assert result.mock_allowed is True
    assert result.timeout_seconds >= 1.0


def test_external_validation_request_defaults_to_configured_provider_when_omitted(monkeypatch):
    import routers.external_validation as external_validation

    monkeypatch.setattr(external_validation, "_VALIDATION_PROVIDER", "serasa")

    assert external_validation.ExternalValidationRequestIn().provider is None
    assert external_validation._resolve_effective_provider(None) == "serasa"


@pytest.mark.asyncio
async def test_history_filters_are_applied():
    from routers.external_validation import list_external_validation_history

    db = AsyncMock()
    user = _make_user(role="AUDITOR")
    player = _make_player(player_id="p1", tenant_id="t1")

    db.get = AsyncMock(return_value=player)

    execute_rows = MagicMock()
    execute_rows.scalars.return_value.all.return_value = []
    execute_count = MagicMock()
    execute_count.scalar_one.return_value = 0
    db.execute = AsyncMock(side_effect=[execute_rows, execute_count])

    result = await list_external_validation_history(
        player_id="p1",
        limit=10,
        offset=0,
        status="FAILED",
        provider="mock_identity",
        current_user=user,
        db=db,
    )

    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_request_external_validation_blocks_unknown_non_mock_provider_when_mock_configured():
    from routers.external_validation import ExternalValidationRequestIn, request_external_validation

    db = AsyncMock()
    user = _make_user()
    player = _make_player(player_id="p1", tenant_id="t1")

    db.get = AsyncMock(return_value=player)
    db.execute = AsyncMock(return_value=MagicMock())

    body = ExternalValidationRequestIn(provider="serasa", validation_type="CPF_IDENTITY", payload={})

    with pytest.raises(HTTPException) as exc:
        await request_external_validation(
            player_id="p1",
            background_tasks=BackgroundTasks(),
            body=body,
            current_user=user,
            db=db,
        )

    assert exc.value.status_code == 400


def _make_user(user_id: str = "u1", tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = user_id
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_player(player_id: str = "p1", tenant_id: str = "t1"):
    p = MagicMock()
    p.id = player_id
    p.tenant_id = tenant_id
    return p


@pytest.mark.asyncio
async def test_request_external_validation_idempotent_reuse_returns_existing_request():
    from routers.external_validation import ExternalValidationRequestIn, request_external_validation

    db = AsyncMock()
    user = _make_user()
    player = _make_player(player_id="p1", tenant_id="t1")

    existing = MagicMock()
    existing.id = "req-existing"
    existing.status = "COMPLETED"
    existing.response_payload = {"match": True}
    existing.provider = "mock_identity"
    existing.validation_type = "CPF_IDENTITY"
    existing.requested_at = None
    existing.completed_at = None
    existing.error_message = None

    db.get = AsyncMock(return_value=player)
    execute_result = MagicMock()
    execute_result.scalars.return_value.first.return_value = existing
    db.execute = AsyncMock(return_value=execute_result)

    body = ExternalValidationRequestIn(provider="mock_identity", validation_type="CPF_IDENTITY", payload={})
    result = await request_external_validation(
        player_id="p1",
        background_tasks=BackgroundTasks(),
        body=body,
        current_user=user,
        db=db,
    )

    assert result["request_id"] == "req-existing"
    assert result["idempotent_reuse"] is True


@pytest.mark.asyncio
async def test_retry_external_validation_requires_failed_status():
    from routers.external_validation import retry_external_validation

    db = AsyncMock()
    user = _make_user(role="ADMIN")

    req = MagicMock()
    req.id = "req1"
    req.tenant_id = "t1"
    req.status = "COMPLETED"

    db.get = AsyncMock(return_value=req)

    with pytest.raises(HTTPException) as exc:
        await retry_external_validation(
            request_id="req1",
            background_tasks=BackgroundTasks(),
            current_user=user,
            db=db,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_retry_external_validation_queues_new_request_when_failed():
    from routers.external_validation import retry_external_validation

    db = AsyncMock()
    user = _make_user(role="ADMIN")

    req = MagicMock()
    req.id = "req-failed"
    req.tenant_id = "t1"
    req.player_id = "p1"
    req.provider = "mock_identity"
    req.validation_type = "CPF_IDENTITY"
    req.status = "FAILED"
    req.request_payload = {"k": "v"}

    db.get = AsyncMock(return_value=req)
    db.add = MagicMock()
    db.commit = AsyncMock()

    result = await retry_external_validation(
        request_id="req-failed",
        background_tasks=BackgroundTasks(),
        current_user=user,
        db=db,
    )

    assert result["status"] == "QUEUED"
    assert result["retries_from"] == "req-failed"
    added_objs = [c.args[0] for c in db.add.call_args_list]
    assert any(obj.__class__.__name__ == "ExternalValidationRequest" for obj in added_objs)
    db.commit.assert_awaited_once()
