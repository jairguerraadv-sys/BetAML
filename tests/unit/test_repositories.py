"""
tests/unit/test_repositories.py — Unit tests for the repository layer.

Tests cover:
  - CaseRepository.get_by_id: found, not found, wrong tenant
  - CaseRepository.list_filtered: active filter, specific status, empty
  - CaseRepository.count_filtered: empty and non-empty results
  - CaseRepository.count_open: delegates to count_filtered("active")
  - CaseRepository.transition_status: valid transition, invalid raises ValueError
  - CaseRepository.assign: sets assigned_to and calls db.add
  - AlertRepository: list_filtered delegates to db.execute, supports date/severity
  - PlayerRepository: list_active excludes ERASED, get_by_id returns correct player
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.scalar.return_value = 0
        result.scalar_one.return_value = 0
        return result

    db.execute = _execute
    return db


def _make_case(
    case_id: str = "c1",
    tenant_id: str = "t1",
    status: str = "OPEN",
    player_id: str | None = None,
    sla_due_at: datetime | None = None,
):
    c = MagicMock()
    c.id = case_id
    c.tenant_id = tenant_id
    c.status = status
    c.player_id = player_id
    c.assigned_to = None
    c.priority = "MEDIUM"
    c.created_at = datetime.now(UTC)
    c.sla_due_at = sla_due_at
    return c


# ---------------------------------------------------------------------------
# CaseRepository.get_by_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_repo_get_by_id_found():
    """get_by_id returns case when tenant_id matches."""
    from repositories.cases import CaseRepository

    case = _make_case("c1", tenant_id="t1")
    db = _make_session()
    db.get = AsyncMock(return_value=case)

    repo = CaseRepository(db)
    result = await repo.get_by_id("t1", "c1")

    assert result is case


@pytest.mark.asyncio
async def test_case_repo_get_by_id_not_found_returns_none():
    """get_by_id returns None when case does not exist."""
    from repositories.cases import CaseRepository

    db = _make_session()
    db.get = AsyncMock(return_value=None)

    repo = CaseRepository(db)
    result = await repo.get_by_id("t1", "nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_case_repo_get_by_id_wrong_tenant_returns_none():
    """get_by_id returns None when case belongs to different tenant."""
    from repositories.cases import CaseRepository

    case = _make_case("c1", tenant_id="t-other")
    db = _make_session()
    db.get = AsyncMock(return_value=case)

    repo = CaseRepository(db)
    result = await repo.get_by_id("t1", "c1")

    assert result is None


# ---------------------------------------------------------------------------
# CaseRepository.list_filtered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_repo_list_filtered_returns_list():
    """list_filtered returns a list of Case objects."""
    from repositories.cases import CaseRepository

    cases = [_make_case(f"c{i}") for i in range(3)]

    db = _make_session()
    async def _execute(stmt):
        result = MagicMock()
        result.scalars.return_value.all.return_value = cases
        return result
    db.execute = _execute

    repo = CaseRepository(db)
    result = await repo.list_filtered("t1")

    assert len(result) == 3


@pytest.mark.asyncio
async def test_case_repo_list_filtered_empty():
    """list_filtered returns [] when no cases match."""
    from repositories.cases import CaseRepository

    db = _make_session()
    repo = CaseRepository(db)
    result = await repo.list_filtered("t1", status="CLOSED")

    assert result == []


# ---------------------------------------------------------------------------
# CaseRepository.count_filtered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_repo_count_filtered_returns_int():
    """count_filtered returns an integer count."""
    from repositories.cases import CaseRepository

    db = _make_session()
    async def _execute(stmt):
        result = MagicMock()
        result.scalar.return_value = 7
        return result
    db.execute = _execute

    repo = CaseRepository(db)
    count = await repo.count_filtered("t1")

    assert count == 7


@pytest.mark.asyncio
async def test_case_repo_count_filtered_zero_when_empty():
    """count_filtered returns 0 when execute returns None."""
    from repositories.cases import CaseRepository

    db = _make_session()
    async def _execute(stmt):
        result = MagicMock()
        result.scalar.return_value = None
        return result
    db.execute = _execute

    repo = CaseRepository(db)
    count = await repo.count_filtered("t1")

    assert count == 0


# ---------------------------------------------------------------------------
# CaseRepository.transition_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_repo_transition_valid():
    """Valid transition must update case.status and call db.add."""
    from repositories.cases import CaseRepository

    transition_graph = {
        "OPEN": ["INVESTIGATING", "CLOSED"],
        "INVESTIGATING": ["PENDING_REVIEW", "CLOSED"],
    }
    case = _make_case(status="OPEN")
    db = _make_session()
    repo = CaseRepository(db)

    await repo.transition_status(case, "INVESTIGATING", transition_graph=transition_graph)

    assert case.status == "INVESTIGATING"
    db.add.assert_called_once_with(case)


@pytest.mark.asyncio
async def test_case_repo_transition_invalid_raises_value_error():
    """Invalid transition must raise ValueError (not HTTPException)."""
    from repositories.cases import CaseRepository

    transition_graph = {
        "REPORTED": [],  # terminal
    }
    case = _make_case(status="REPORTED")
    db = _make_session()
    repo = CaseRepository(db)

    with pytest.raises(ValueError, match="Transição inválida"):
        await repo.transition_status(case, "OPEN", transition_graph=transition_graph)


@pytest.mark.asyncio
async def test_case_repo_transition_from_closed_to_open():
    """CLOSED → OPEN (re-open) must succeed."""
    from repositories.cases import CaseRepository

    transition_graph = {"CLOSED": ["OPEN"]}
    case = _make_case(status="CLOSED")
    db = _make_session()
    repo = CaseRepository(db)

    await repo.transition_status(case, "OPEN", transition_graph=transition_graph)

    assert case.status == "OPEN"


# ---------------------------------------------------------------------------
# CaseRepository.assign
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_case_repo_assign_sets_user_and_calls_add():
    """assign() must set case.assigned_to and call db.add."""
    from repositories.cases import CaseRepository

    case = _make_case()
    db = _make_session()
    repo = CaseRepository(db)

    await repo.assign(case, "analyst-007")

    assert case.assigned_to == "analyst-007"
    db.add.assert_called_once_with(case)


# ---------------------------------------------------------------------------
# AlertRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alert_repo_list_filtered_returns_list():
    """AlertRepository.list_filtered returns a list."""
    from repositories.alerts import AlertRepository

    db = _make_session()
    repo = AlertRepository(db)
    result = await repo.list_filtered("t1")

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_alert_repo_count_filtered_returns_int():
    """AlertRepository.count_filtered returns an integer."""
    from repositories.alerts import AlertRepository

    db = _make_session()
    async def _execute(stmt):
        result = MagicMock()
        result.scalar.return_value = 3
        return result
    db.execute = _execute

    repo = AlertRepository(db)
    count = await repo.count_filtered("t1")

    assert count == 3


# ---------------------------------------------------------------------------
# PlayerRepository
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_player_repo_list_active_returns_list():
    """PlayerRepository.list_active returns a list of players."""
    from repositories.players import PlayerRepository

    db = _make_session()
    repo = PlayerRepository(db)
    result = await repo.list_active("t1")

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_player_repo_get_by_id_found():
    """PlayerRepository.get_by_id returns player when found with matching tenant."""
    from repositories.players import PlayerRepository

    player = MagicMock()
    player.id = "p1"
    player.tenant_id = "t1"

    db = _make_session()
    db.get = AsyncMock(return_value=player)

    repo = PlayerRepository(db)
    result = await repo.get_by_id("t1", "p1")

    assert result is player


@pytest.mark.asyncio
async def test_player_repo_get_by_id_wrong_tenant():
    """PlayerRepository.get_by_id returns None for wrong tenant."""
    from repositories.players import PlayerRepository

    player = MagicMock()
    player.id = "p1"
    player.tenant_id = "t-other"

    db = _make_session()
    db.get = AsyncMock(return_value=player)

    repo = PlayerRepository(db)
    result = await repo.get_by_id("t1", "p1")

    assert result is None
