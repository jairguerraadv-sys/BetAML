"""
tests/unit/test_module10_security.py — Module 10 security coverage.

Covers:
  - AUDITOR cannot create rules
  - AUDITOR cannot create mappings
  - player detail masks CPF for AUDITOR
  - player detail shows full CPF for AML roles
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _make_user(role: str = "AUDITOR", tenant_id: str = "t1"):
    user = MagicMock()
    user.id = "u1"
    user.role = role
    user.tenant_id = tenant_id
    user.roles = None  # força fallback ao mapa legado em get_effective_roles
    return user


def _make_player():
    player = MagicMock()
    player.id = "p1"
    player.tenant_id = "t1"
    player.status = "ACTIVE"
    player.external_player_id = "ext-1"
    player.cpf_encrypted = b"cipher"
    player.pep_flag = False
    player.risk_score = 0.82
    player.risk_band = "HIGH"
    player.declared_income_monthly = None
    player.last_scored_at = None
    return player


@pytest.mark.asyncio
async def test_auditor_cannot_create_rule():
    from routers.rules import create_rule

    dependency = inspect.signature(create_rule).parameters["current_user"].default.dependency
    with pytest.raises(HTTPException) as exc_info:
        await dependency(current_user=_make_user(role="AUDITOR"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_auditor_cannot_create_mapping():
    from routers.mappings import create_mapping

    dependency = inspect.signature(create_mapping).parameters["current_user"].default.dependency
    with pytest.raises(HTTPException) as exc_info:
        await dependency(current_user=_make_user(role="AUDITOR"))

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_player_masks_cpf_for_auditor():
    from routers.players import get_player

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_make_player())
    db = AsyncMock()
    db.flush = AsyncMock()

    # Configura db.execute para retornar resultado compatível com .scalars().first() e .scalar()
    _exec_result = MagicMock()
    _exec_result.scalars.return_value.first.return_value = None
    _exec_result.scalar.return_value = 0
    db.execute = AsyncMock(return_value=_exec_result)

    with patch("routers.players.decrypt_pii", return_value="12345678909"), \
         patch("routers.players.write_audit", new_callable=AsyncMock):
        result = await get_player(
            player_id="p1",
            current_user=_make_user(role="AUDITOR"),
            repo=repo,
            db=db,
        )

    assert result["cpf"] == "***.***.***.09"


@pytest.mark.asyncio
async def test_get_player_returns_full_cpf_for_aml_analyst():
    from routers.players import get_player

    repo = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=_make_player())
    db = AsyncMock()
    db.flush = AsyncMock()

    # Configura db.execute para retornar resultado compatível com .scalars().first() e .scalar()
    _exec_result = MagicMock()
    _exec_result.scalars.return_value.first.return_value = None
    _exec_result.scalar.return_value = 0
    db.execute = AsyncMock(return_value=_exec_result)

    with patch("routers.players.decrypt_pii", return_value="12345678909"), \
         patch("routers.players.write_audit", new_callable=AsyncMock):
        result = await get_player(
            player_id="p1",
            current_user=_make_user(role="AML_ANALYST"),
            repo=repo,
            db=db,
        )

    assert result["cpf"] == "12345678909"
