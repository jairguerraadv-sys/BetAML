"""
tests/unit/test_feature_store.py — Unit tests for routers/feature_store.py

Tests cover:
  - GET /feature-store/population-stats: Redis miss → empty response
  - GET /feature-store/population-stats: Redis hit → parsed stats, count defaults to 0
  - Router path registrations (history, current, population-stats)
  - Schema structure for FeatureSnapshotOut, FeatureStoreHistoryItemOut,
    FeaturePopulationStatsOut, FeatureStat
"""
from __future__ import annotations

import json
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "t1"):
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = tenant_id
    u.role = "AML_ANALYST"
    return u


def _make_redis(get_return=None):
    """Async Redis mock: .get() returns get_return, .aclose() is a no-op."""
    r = AsyncMock()
    r.get = AsyncMock(return_value=get_return)
    r.aclose = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# GET /feature-store/population-stats
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_population_stats_empty_redis():
    """Redis miss (None) → HTTP 200 with {computed_at: None, features: {}}."""
    from routers.feature_store import get_feature_population_stats

    redis_mock = _make_redis(get_return=None)
    user = _make_user()

    with patch("redis.asyncio.from_url", return_value=redis_mock):
        result = await get_feature_population_stats(current_user=user)

    assert result.computed_at is None
    assert result.features == {}


@pytest.mark.asyncio
async def test_get_population_stats_returns_data():
    """Redis hit with valid JSON → parses and returns FeaturePopulationStatsOut."""
    from routers.feature_store import get_feature_population_stats

    raw_stats = {
        "deposit_sum_30d": {
            "mean": 150.0,
            "std": 50.0,
            "p10": 50.0,
            "p25": 100.0,
            "p50": 150.0,
            "p75": 200.0,
            "p90": 250.0,
        },
        "chargeback_count_30d": {
            "mean": 0.5,
            "std": 0.1,
            "p10": 0.0,
            "p25": 0.2,
            "p50": 0.5,
            "p75": 0.8,
            "p90": 1.0,
        },
    }
    redis_mock = _make_redis(get_return=json.dumps(raw_stats))
    user = _make_user("tenant-abc")

    with patch("redis.asyncio.from_url", return_value=redis_mock):
        result = await get_feature_population_stats(current_user=user)

    assert "deposit_sum_30d" in result.features
    assert "chargeback_count_30d" in result.features
    stat = result.features["deposit_sum_30d"]
    assert stat.mean == pytest.approx(150.0)
    assert stat.std == pytest.approx(50.0)
    assert stat.p50 == pytest.approx(150.0)
    # count not in Redis JSON — must default to 0
    assert stat.count == 0


@pytest.mark.asyncio
async def test_get_population_stats_redis_key_uses_tenant_id():
    """Endpoint must read from feature_stats:{tenant_id} key for tenant isolation."""
    from routers.feature_store import get_feature_population_stats

    redis_mock = _make_redis(get_return=None)
    user = _make_user("my-tenant-99")

    with patch("redis.asyncio.from_url", return_value=redis_mock):
        await get_feature_population_stats(current_user=user)

    redis_mock.get.assert_awaited_once_with("feature_stats:my-tenant-99")


# ---------------------------------------------------------------------------
# Router path registrations
# ---------------------------------------------------------------------------

def test_feature_store_history_router_has_path():
    """GET /feature-store/players/{player_id}/history must be registered."""
    from routers.feature_store import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/feature-store/players/{player_id}/history" in paths


def test_current_router_has_path():
    """GET /feature-store/players/{player_id}/current must be registered."""
    from routers.feature_store import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/feature-store/players/{player_id}/current" in paths


def test_population_stats_router_has_path():
    """GET /feature-store/population-stats must be registered."""
    from routers.feature_store import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/feature-store/population-stats" in paths


# ---------------------------------------------------------------------------
# Schema structure
# ---------------------------------------------------------------------------

def test_feature_snapshot_out_has_feature_version():
    """FeatureSnapshotOut must carry feature_version for version tracking."""
    from libs.schemas import FeatureSnapshotOut
    fields = FeatureSnapshotOut.model_fields
    assert "feature_version" in fields
    assert fields["feature_version"].default == 1


def test_feature_store_history_item_has_drift_score():
    """FeatureStoreHistoryItemOut must expose drift_score (nullable float)."""
    from libs.schemas import FeatureStoreHistoryItemOut
    fields = FeatureStoreHistoryItemOut.model_fields
    assert "drift_score" in fields
    assert fields["drift_score"].default is None


def test_population_stats_schema_structure():
    """FeaturePopulationStatsOut and FeatureStat must have correct fields and defaults."""
    from libs.schemas import FeaturePopulationStatsOut, FeatureStat

    # FeatureStat validates numeric stats; count defaults to 0
    stat = FeatureStat(
        mean=1.0, std=0.5,
        p10=0.1, p25=0.25, p50=0.5, p75=0.75, p90=0.9,
    )
    assert stat.count == 0  # default — not stored in Redis

    # FeaturePopulationStatsOut: empty is valid
    empty = FeaturePopulationStatsOut()
    assert empty.computed_at is None
    assert empty.features == {}

    # FeaturePopulationStatsOut: with data
    with_data = FeaturePopulationStatsOut(
        computed_at="2026-03-14T06:00:00+00:00",
        features={"deposit_sum_30d": stat},
    )
    assert with_data.features["deposit_sum_30d"].mean == pytest.approx(1.0)
    assert with_data.features["deposit_sum_30d"].count == 0
