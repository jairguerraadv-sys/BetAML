from __future__ import annotations

import os
import sys
import importlib.util
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _load_api_main_module():
    module_name = "betaml_api_main_runtime"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = os.path.join(os.path.dirname(__file__), "../../services/api/main.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    sys.modules[module_name] = module
    return module


def _session_with_execute(execute_fn):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = execute_fn
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_warm_feature_store_cache_restores_latest_snapshot_metadata():
    api_main = _load_api_main_module()

    redis_mock = AsyncMock()
    redis_mock.hset = AsyncMock()
    redis_mock.expire = AsyncMock()
    redis_mock.aclose = AsyncMock()

    calls: list[int] = []

    async def execute(stmt, params=None):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = ["tenant-1"]
        elif len(calls) == 2:
            result.scalar_one_or_none.return_value = None
        else:
            result.mappings.return_value.all.return_value = [
                {
                    "tenant_id": "tenant-1",
                    "player_id": "player-1",
                    "feature_date": date(2026, 3, 20),
                    "created_at": "2026-03-20T06:00:00+00:00",
                    "features": {
                        "deposit_sum_24h": 1000.0,
                        "feature_version": 2,
                    },
                }
            ]
        return result

    session = _session_with_execute(execute)

    with patch.object(api_main, "AsyncSessionLocal", return_value=session), patch(
        "redis.asyncio.from_url", return_value=redis_mock
    ):
        await api_main._warm_feature_store_cache()

    redis_mock.hset.assert_awaited_once()
    key = redis_mock.hset.call_args.args[0]
    mapping = redis_mock.hset.call_args.kwargs["mapping"]
    assert key == "betaml:tenant-1:features:player-1"
    assert mapping["snapshot_date"] == "2026-03-20"
    assert mapping["entity_type"] == "PLAYER"
    assert mapping["snapshot_version"] == "2"
    assert mapping["gold_object_path"].startswith("gold/tenant_id=tenant-1/feature_date=2026-03-20/")
    assert mapping["warmed_from"] == "feature_snapshot"
    redis_mock.expire.assert_awaited_once_with(key, 14400)


@pytest.mark.asyncio
async def test_feature_drift_check_creates_admin_notification_and_marks_rows():
    api_main = _load_api_main_module()

    current_row = SimpleNamespace(
        tenant_id="tenant-1",
        feature_date=date(2026, 3, 20),
        features={"deposit_sum_24h": 1000.0, "night_activity_ratio": None},
        drift_score=None,
    )
    previous_row = SimpleNamespace(
        tenant_id="tenant-1",
        feature_date=date(2026, 3, 19),
        features={"deposit_sum_24h": 100.0, "night_activity_ratio": 0.1},
        drift_score=None,
    )

    calls: list[int] = []

    async def execute(stmt, params=None):
        result = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            result.scalars.return_value.all.return_value = ["tenant-1"]
        elif len(calls) == 2:
            result.scalars.return_value.all.return_value = [date(2026, 3, 20), date(2026, 3, 19)]
        elif len(calls) == 3:
            result.scalars.return_value.all.return_value = [current_row]
        elif len(calls) == 4:
            result.scalars.return_value.all.return_value = [previous_row]
        elif len(calls) == 5:
            result.scalar_one_or_none.return_value = None
        else:
            result.scalars.return_value.all.return_value = ["admin-1"]
        return result

    session = _session_with_execute(execute)

    with patch.object(api_main, "AsyncSessionLocal", return_value=session):
        await api_main._run_feature_drift_check_once()

    session.commit.assert_awaited_once()
    assert current_row.drift_score is not None
    assert current_row.drift_score >= 0.5

    notification = session.add.call_args.args[0]
    assert notification.type == "FEATURE_DRIFT"
    assert notification.user_id == "admin-1"
    assert "Drift de features detectado" in notification.title
