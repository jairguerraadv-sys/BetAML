"""
tests/unit/test_jobs.py — Unit tests for APScheduler background jobs.

Tests cover:
  - Risk score decay with active/inactive players
  - LGPD data expiration respecting per-tenant data_retention_days
  - Edge cases: no tenants, missing ScoringConfig, ERASED players skipped
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _make_mock_session(tenants=None, scoring_cfg=None, players=None, alerts=None):
    """Build a fully mocked AsyncSession for jobs.py tests."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()

    call_log = []

    async def _execute(stmt):
        result = MagicMock()
        call_log.append(str(stmt)[:60])
        # Return different results based on call order
        idx = len(call_log) - 1
        if idx == 0:
            # First call: SELECT tenants
            result.scalars.return_value.all.return_value = tenants or []
        elif idx == 1:
            # Second call: SELECT scoring_config
            result.scalar_one_or_none.return_value = scoring_cfg
        elif idx == 2:
            # Third call: SELECT players
            result.scalars.return_value.all.return_value = players or []
        else:
            # Subsequent calls: SELECT alerts for each player
            result.scalars.return_value.all.return_value = alerts or []
        return result

    session.execute = _execute
    return session


def _tenant(tenant_id="t1"):
    t = MagicMock()
    t.id = tenant_id
    t.active = True
    return t


def _scoring_cfg(rule_w=0.4, ml_w=0.4, net_w=0.2, retention=365):
    sc = MagicMock()
    sc.rule_weight = Decimal(str(rule_w))
    sc.ml_weight = Decimal(str(ml_w))
    sc.network_weight = Decimal(str(net_w))
    sc.data_retention_days = retention
    return sc


def _player(pid="p1", risk_score=0.8, last_scored_days_ago=None):
    p = MagicMock()
    p.id = pid
    p.risk_score = Decimal(str(risk_score))
    p.status = "ACTIVE"
    if last_scored_days_ago is not None:
        p.last_scored_at = datetime.now(UTC) - timedelta(days=last_scored_days_ago)
    else:
        p.last_scored_at = datetime.now(UTC) - timedelta(days=10)
    return p


def _alert(severity="HIGH", anomaly_score=0.8, has_shared=False):
    a = MagicMock()
    a.severity = severity
    a.anomaly_score = Decimal(str(anomaly_score))
    a.evidence = {"shared_device": True} if has_shared else {}
    return a


# ─────────────────────────────────────────────────────────────────────────────
# Risk Score Decay Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_score_decay_no_recent_alerts_reduces_score():
    """Player with no alerts in 30d should have risk_score *= 0.95."""
    from jobs import calculate_risk_score_decay

    tenant = _tenant()
    sc = _scoring_cfg()
    player = _player(risk_score=0.8)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()

    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = sc
        elif len(calls) == 3:
            result.scalars.return_value.all.return_value = [player]
        else:
            result.scalars.return_value.all.return_value = []  # no alerts
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await calculate_risk_score_decay()

    expected = Decimal(str(round(0.8 * 0.95, 4)))
    assert float(player.risk_score) == pytest.approx(float(expected), rel=1e-5)


@pytest.mark.asyncio
async def test_risk_score_decay_with_critical_alerts_computes_weighted_score():
    """Player with CRITICAL alerts should have weighted risk score computed."""
    from jobs import calculate_risk_score_decay

    tenant = _tenant()
    sc = _scoring_cfg(rule_w=0.4, ml_w=0.4, net_w=0.2)
    player = _player(risk_score=0.5)
    alerts = [_alert("CRITICAL", anomaly_score=0.9), _alert("CRITICAL", anomaly_score=0.85)]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = sc
        elif len(calls) == 3:
            result.scalars.return_value.all.return_value = [player]
        else:
            result.scalars.return_value.all.return_value = alerts
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await calculate_risk_score_decay()

    # rule_score: CRITICAL=1.0 avg => 1.0 * 0.4 = 0.4
    # ml_score: avg(0.9, 0.85) = 0.875 * 0.4 = 0.35
    # network_score: 0 (no shared evidence) * 0.2 = 0.0
    # total = 0.75
    assert float(player.risk_score) == pytest.approx(0.75, abs=0.01)


@pytest.mark.asyncio
async def test_risk_score_decay_score_capped_at_1():
    """Weighted score must never exceed 1.0."""
    from jobs import calculate_risk_score_decay

    tenant = _tenant()
    sc = _scoring_cfg(rule_w=0.9, ml_w=0.9, net_w=0.9)  # weights > 1 intentionally
    player = _player(risk_score=0.3)
    alerts = [_alert("CRITICAL", 1.0), _alert("CRITICAL", 1.0)]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = sc
        elif len(calls) == 3:
            result.scalars.return_value.all.return_value = [player]
        else:
            result.scalars.return_value.all.return_value = alerts
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await calculate_risk_score_decay()

    assert float(player.risk_score) <= 1.0


@pytest.mark.asyncio
async def test_risk_score_decay_no_scoring_config_skips_tenant():
    """If ScoringConfig is missing for a tenant, skip that tenant gracefully."""
    from jobs import calculate_risk_score_decay

    tenant = _tenant()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        else:
            result.scalar_one_or_none.return_value = None  # No ScoringConfig
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await calculate_risk_score_decay()  # Must not raise

    # When scoring_cfg is missing, the tenant loop continues without committing
    session.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# LGPD Data Expiration Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lgpd_expiration_anonymizes_expired_player():
    """Player whose last_scored_at > data_retention_days should be anonymized."""
    from jobs import cleanup_expired_player_data

    tenant = _tenant()
    sc = _scoring_cfg(retention=365)
    # Player last scored 500 days ago (> 365 retention)
    player = _player(last_scored_days_ago=500)
    player.cpf_encrypted = b"REAL_CPF_DATA"
    player.name_encrypted = b"REAL_NAME"

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = sc
        else:
            result.scalars.return_value.all.return_value = [player]
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await cleanup_expired_player_data()

    assert player.status == "ERASED"
    assert player.cpf_encrypted.startswith(b"ERASURE_")
    assert player.name_encrypted.startswith(b"ERASURE_")


@pytest.mark.asyncio
async def test_lgpd_expiration_respects_data_retention_days():
    """Player within retention window must NOT be anonymized."""
    from jobs import cleanup_expired_player_data

    tenant = _tenant()
    sc = _scoring_cfg(retention=730)  # 2 years
    # Player last scored 400 days ago (< 730 retention) → should NOT be erased
    player = _player(last_scored_days_ago=400)
    player.cpf_encrypted = b"REAL_CPF_DATA"
    player.name_encrypted = b"REAL_NAME"

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = sc
        else:
            result.scalars.return_value.all.return_value = []  # query returns no expired players
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await cleanup_expired_player_data()

    # Player should NOT be erased since query returned []
    assert player.status != "ERASED"
    assert player.cpf_encrypted == b"REAL_CPF_DATA"


@pytest.mark.asyncio
async def test_lgpd_expiration_skips_tenant_without_scoring_config():
    """Tenant without ScoringConfig must be skipped without error."""
    from jobs import cleanup_expired_player_data

    tenant = _tenant()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    calls = []

    async def execute(stmt):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = [tenant]
        else:
            result.scalar_one_or_none.return_value = None  # No ScoringConfig
        return result

    session.execute = execute

    with patch("jobs.AsyncSessionLocal", return_value=session):
        await cleanup_expired_player_data()  # Must not raise

    # When scoring_cfg is missing, the tenant loop continues without committing
    session.commit.assert_not_called()
