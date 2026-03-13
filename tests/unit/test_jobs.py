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


# ─────────────────────────────────────────────────────────────────────────────
# SLA Violation Monitor Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckSlaViolations:
    """Unit tests for check_sla_violations() — hourly SLA monitor."""

    @staticmethod
    def _make_case(status="OPEN", assigned_to=None, hours_overdue=3):
        case = MagicMock()
        case.id = "case-sla-1"
        case.tenant_id = "t1"
        case.title = "Suspicious Account Activity"
        case.reference_number = "REF-2024-001"
        case.status = status
        case.assigned_to = assigned_to
        case.sla_due_at = datetime.now(UTC) - timedelta(hours=hours_overdue)
        return case

    @staticmethod
    def _make_user(uid="admin-1"):
        u = MagicMock()
        u.id = uid
        u.tenant_id = "t1"
        u.role = "ADMIN"
        u.active = True
        return u

    @staticmethod
    def _make_session(execute_fn):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.commit = AsyncMock()
        session.add = MagicMock()
        session.execute = execute_fn
        return session

    @pytest.mark.asyncio
    async def test_overdue_case_creates_notification_and_audit_log(self):
        """
        Case past SLA with no prior notification: one Notification and one AuditLog
        are added, then commit() is called.

        Query sequence:
          1) SELECT overdue cases → [case]
          2) SELECT ADMIN/AML_ANALYST users for tenant → [admin]
          3) SELECT dedup notification for (case, admin) → None  (no prior notification)
        """
        from jobs import check_sla_violations

        case = self._make_case(status="OPEN")   # assigned_to=None → notif_user_ids built from admins only
        admin = self._make_user("admin-1")

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                # SELECT overdue cases
                result.scalars.return_value.all.return_value = [case]
            elif len(calls) == 2:
                # SELECT admins/analysts for the case's tenant
                result.scalars.return_value.all.return_value = [admin]
            else:
                # Dedup check: no recent SLA_VIOLATION notification exists
                result.scalar_one_or_none.return_value = None
            return result

        session = self._make_session(execute)

        with patch("jobs.AsyncSessionLocal", return_value=session):
            await check_sla_violations()

        # db.add called twice: once for Notification, once for AuditLog
        assert session.add.call_count == 2
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_dedup_within_2h_skips_notification_audit_log_still_added(self):
        """
        Case already notified within the last 2 h: the dedup check finds an existing
        Notification and skips creating a new one.  The AuditLog (outside the uid loop)
        is still added and commit() is still called.

        Query sequence:
          1) SELECT overdue cases → [case]
          2) SELECT admins for tenant → [admin]
          3) SELECT dedup → existing_notif  (→ skip Notification)
        """
        from jobs import check_sla_violations

        case = self._make_case(status="IN_REVIEW")   # assigned_to=None
        admin = self._make_user("admin-1")
        recent_notif = MagicMock()    # stands in for the existing Notification row

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                result.scalars.return_value.all.return_value = [case]
            elif len(calls) == 2:
                result.scalars.return_value.all.return_value = [admin]
            else:
                # Dedup: a SLA_VIOLATION notification was already sent within 2 h
                result.scalar_one_or_none.return_value = recent_notif
            return result

        session = self._make_session(execute)

        with patch("jobs.AsyncSessionLocal", return_value=session):
            await check_sla_violations()

        # Only AuditLog added; Notification skipped by the dedup guard
        assert session.add.call_count == 1
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_overdue_cases_returns_early_without_writes(self):
        """
        When no OPEN/IN_REVIEW cases have a past sla_due_at, the job returns
        immediately after the first query without any db.add() or commit() calls.
        """
        from jobs import check_sla_violations

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            # First (and only) query returns an empty result set
            result.scalars.return_value.all.return_value = []
            return result

        session = self._make_session(execute)

        with patch("jobs.AsyncSessionLocal", return_value=session):
            await check_sla_violations()

        session.add.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminal_status_cases_excluded_no_notifications(self):
        """
        Cases with CLOSED, REPORTED, or ARCHIVED status are excluded by the
        WHERE status IN ('OPEN', 'IN_REVIEW') clause in the query.
        Simulated by the mock returning an empty result set, which triggers the
        same early-return path and produces no notifications or audit writes.
        """
        from jobs import check_sla_violations

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            # CLOSED / REPORTED / ARCHIVED cases do not match status IN ('OPEN', 'IN_REVIEW')
            result.scalars.return_value.all.return_value = []
            return result

        session = self._make_session(execute)

        with patch("jobs.AsyncSessionLocal", return_value=session):
            await check_sla_violations()

        session.add.assert_not_called()
        session.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Feature Population Stats Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeFeaturePopulationStats:
    """Unit tests for compute_feature_population_stats() — daily 06:00 UTC job."""

    @staticmethod
    def _make_session(execute_fn):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = execute_fn
        return session

    @staticmethod
    def _make_redis():
        redis_mock = AsyncMock()
        redis_mock.set = AsyncMock()
        redis_mock.aclose = AsyncMock()
        return redis_mock

    @pytest.mark.asyncio
    async def test_snapshots_exist_stores_stats_dict_in_redis(self):
        """
        When FeatureSnapshots exist for a tenant, the job accumulates numeric values
        per feature key, computes mean / std / percentiles, serialises the result as
        JSON and stores it in Redis under 'feature_stats:{tenant_id}'.

        Concrete assertions (two snapshots with values [100.0, 200.0]):
          mean  = 150.0
          std   = 50.0   (population stdev)
          p50   = 150.0
        """
        import json as _json
        from jobs import compute_feature_population_stats

        tenant = _tenant("t1")

        snap1 = MagicMock()
        snap1.features = {"deposit_sum_30d": 100.0, "tx_count_30d": 10.0}
        snap2 = MagicMock()
        snap2.features = {"deposit_sum_30d": 200.0, "tx_count_30d": 20.0}

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                # SELECT active tenants
                result.scalars.return_value.all.return_value = [tenant]
            else:
                # SELECT feature_snapshots for tenant
                result.scalars.return_value.all.return_value = [snap1, snap2]
            return result

        session = self._make_session(execute)
        redis_mock = self._make_redis()

        with patch("jobs.AsyncSessionLocal", return_value=session), \
             patch("redis.asyncio.from_url", return_value=redis_mock):
            await compute_feature_population_stats()

        redis_mock.set.assert_called_once()
        call_args = redis_mock.set.call_args
        assert call_args.args[0] == "feature_stats:t1"
        assert call_args.kwargs["ex"] == 25 * 3600

        stored = _json.loads(call_args.args[1])
        assert "deposit_sum_30d" in stored
        assert "tx_count_30d" in stored

        dep = stored["deposit_sum_30d"]
        # mean([100, 200]) == 150
        assert dep["mean"] == pytest.approx(150.0, abs=0.01)
        # pstdev([100, 200]) == 50
        assert dep["std"] == pytest.approx(50.0, abs=0.01)
        # p50 (median via linear interpolation) == 150
        assert dep["p50"] == pytest.approx(150.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_no_snapshots_does_not_write_to_redis(self):
        """
        When the 30-day FeatureSnapshot window is empty for a tenant, the job
        executes the `continue` branch and does NOT call Redis.set — the tenant
        is silently skipped without crashing.
        """
        from jobs import compute_feature_population_stats

        tenant = _tenant("t1")

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                result.scalars.return_value.all.return_value = [tenant]
            else:
                # No feature snapshots within the last 30 days
                result.scalars.return_value.all.return_value = []
            return result

        session = self._make_session(execute)
        redis_mock = self._make_redis()

        with patch("jobs.AsyncSessionLocal", return_value=session), \
             patch("redis.asyncio.from_url", return_value=redis_mock):
            await compute_feature_population_stats()

        # `if not snapshots: continue` skips the Redis write entirely
        redis_mock.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_set_uses_25_hour_ttl(self):
        """
        Redis.set must be called with ex=25*3600 (90 000 s) so that the cached
        stats outlast one missed daily run (24 h gap).
        """
        from jobs import compute_feature_population_stats

        tenant = _tenant("t1")

        snap = MagicMock()
        snap.features = {"amount": 42.0}

        calls = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                result.scalars.return_value.all.return_value = [tenant]
            else:
                result.scalars.return_value.all.return_value = [snap]
            return result

        session = self._make_session(execute)
        redis_mock = self._make_redis()

        with patch("jobs.AsyncSessionLocal", return_value=session), \
             patch("redis.asyncio.from_url", return_value=redis_mock):
            await compute_feature_population_stats()

        redis_mock.set.assert_called_once()
        assert redis_mock.set.call_args.kwargs["ex"] == 25 * 3600

    @pytest.mark.asyncio
    async def test_snapshots_with_feature_version_attribute_processed_correctly(self):
        """
        GAP-13: FeatureSnapshot rows gained a `feature_version` column in migration v10.
        The population stats job must process snapshots that carry the new attribute
        without errors, and the computed stats should only reflect values from the
        `features` JSONB dict (not from `feature_version` itself).
        """
        import json as _json
        from jobs import compute_feature_population_stats

        tenant = _tenant("t1")

        # Simulate snapshots with feature_version set (as migration v10 adds)
        snap1 = MagicMock()
        snap1.feature_version = 2
        snap1.features = {"deposit_sum_30d": 500.0}

        snap2 = MagicMock()
        snap2.feature_version = 2
        snap2.features = {"deposit_sum_30d": 1000.0}

        calls: list[int] = []

        async def execute(stmt):
            result = MagicMock()
            calls.append(len(calls))
            if len(calls) == 1:
                result.scalars.return_value.all.return_value = [tenant]
            else:
                result.scalars.return_value.all.return_value = [snap1, snap2]
            return result

        session = self._make_session(execute)
        redis_mock = self._make_redis()

        with patch("jobs.AsyncSessionLocal", return_value=session), \
             patch("redis.asyncio.from_url", return_value=redis_mock):
            await compute_feature_population_stats()

        redis_mock.set.assert_called_once()
        stored = _json.loads(redis_mock.set.call_args.args[1])

        # feature_version is NOT a key in the features JSONB; it must not appear in stats
        assert "feature_version" not in stored
        assert "deposit_sum_30d" in stored
        # mean([500, 1000]) == 750
        assert stored["deposit_sum_30d"]["mean"] == pytest.approx(750.0, abs=0.01)

