"""
tests/unit/test_ml_routes.py — Unit tests for routers/ml.py

Tests cover:
  - GET /model-registry: list models filtered by tenant (+ optional model_type)
  - POST /model-registry/{id}/promote: archives champion, promotes challenger, 404 for unknown
  - POST /model-registry/{id}/challenger: sets is_challenger=True, 404 for unknown, 400 for champion
  - ModelRegistryOut schema: model_name Optional, algorithm Optional, version alias works
  - ML performance summary helper aggregates TP/FP trends by rule and model
  - A/B metrics helper compares champion vs challenger over time
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(tenant_id: str = "tenant-1", role: str = "ADMIN") -> MagicMock:
    u = MagicMock()
    u.id = "user-1"
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_model(
    model_id: str = "model-1",
    tenant_id: str = "tenant-1",
    status: str = "STAGING",
    model_type: str = "ANOMALY",
    is_challenger: bool = False,
) -> MagicMock:
    m = MagicMock()
    m.id = model_id
    m.tenant_id = tenant_id
    m.status = status
    m.model_type = model_type
    m.model_name = "IsolationForest"
    m.algorithm = "IsolationForest"
    m.model_version = "20260314030000"
    m.version = "20260314030000"   # @property alias
    m.is_challenger = is_challenger
    m.metrics = {"f1_score": 0.80, "precision": 0.82, "recall": 0.78, "auc_roc": 0.85}
    m.training_rows = 200
    m.feature_columns = []
    m.promoted_by = None
    m.promoted_at = None
    m.trained_by = None
    m.trained_at = None
    m.created_at = None
    return m


def _db_with_scalar(value):
    """DB mock whose first execute returns scalar_one_or_none = value."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _db_with_scalars(values: list):
    """DB mock whose first execute returns scalars().all() = values."""
    db = AsyncMock()
    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = values
    result.scalars.return_value = scalars_result
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


# ── list_models ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_models_returns_tenant_entries():
    from routers.ml import list_models

    model = _make_model()
    db = _db_with_scalars([model])
    user = _make_user()

    result = await list_models(model_type=None, db=db, current_user=user)

    assert isinstance(result, list)
    assert result[0] is model


@pytest.mark.asyncio
async def test_list_models_filters_by_model_type():
    from routers.ml import list_models

    model = _make_model(model_type="SUPERVISED")
    db = _db_with_scalars([model])
    user = _make_user()

    result = await list_models(model_type="SUPERVISED", db=db, current_user=user)
    assert len(result) == 1
    # Verify db.execute was called (filter was applied)
    db.execute.assert_called_once()


# ── promote_model ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_promote_model_archives_champion_and_promotes_model():
    from routers.ml import promote_model

    model = _make_model(model_id="model-a", status="challenger", is_challenger=True)
    # First execute → find model; second execute → bulk update champion
    call_count = [0]
    results = []

    find_result = MagicMock()
    find_result.scalar_one_or_none = MagicMock(return_value=model)
    results.append(find_result)

    bulk_result = MagicMock()
    results.append(bulk_result)

    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()

    async def execute_side_effect(stmt):
        idx = call_count[0]
        call_count[0] += 1
        return results[idx] if idx < len(results) else MagicMock()

    db.execute = execute_side_effect

    with patch("routers.ml._write_audit", new_callable=AsyncMock):
        response = await promote_model("model-a", db=db, current_user=_make_user())

    assert response["status"] == "promoted"
    assert model.status == "champion"
    assert model.is_challenger is False
    assert model.is_active is True


@pytest.mark.asyncio
async def test_promote_model_returns_404_when_not_found():
    from fastapi import HTTPException
    from routers.ml import promote_model

    db = _db_with_scalar(None)  # model not found

    with pytest.raises(HTTPException) as exc_info:
        await promote_model("nonexistent", db=db, current_user=_make_user())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_promote_model_rejects_non_challenger_state():
    from fastapi import HTTPException
    from routers.ml import promote_model

    db = _db_with_scalar(_make_model(model_id="model-a", status="STAGING", is_challenger=False))

    with pytest.raises(HTTPException) as exc_info:
        await promote_model("model-a", db=db, current_user=_make_user())

    assert exc_info.value.status_code == 409


# ── designate_challenger ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_designate_challenger_sets_flag():
    from routers.ml import designate_challenger

    model = _make_model(model_id="model-b", status="STAGING", is_challenger=False)
    champion = _make_model(model_id="champion-a", status="champion", is_challenger=False)

    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    results = []

    find_result = MagicMock()
    find_result.scalar_one_or_none = MagicMock(return_value=model)
    results.append(find_result)

    champion_result = MagicMock()
    champion_result.scalar_one_or_none = MagicMock(return_value=champion)
    results.append(champion_result)

    results.append(MagicMock())

    call_count = [0]

    async def execute_side_effect(stmt):
        idx = call_count[0]
        call_count[0] += 1
        return results[idx]

    db.execute = execute_side_effect

    with patch("routers.ml._write_audit", new_callable=AsyncMock):
        response = await designate_challenger("model-b", db=db, current_user=_make_user())

    assert response["status"] == "challenger"
    assert model.is_challenger is True
    assert model.champion_id == champion.id


@pytest.mark.asyncio
async def test_designate_challenger_returns_404_when_not_found():
    from fastapi import HTTPException
    from routers.ml import designate_challenger

    db = _db_with_scalar(None)

    with pytest.raises(HTTPException) as exc_info:
        await designate_challenger("nonexistent", db=db, current_user=_make_user())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_designate_challenger_returns_400_for_champion():
    from fastapi import HTTPException
    from routers.ml import designate_challenger

    champion = _make_model(model_id="champ", status="champion")
    db = _db_with_scalar(champion)

    with pytest.raises(HTTPException) as exc_info:
        await designate_challenger("champ", db=db, current_user=_make_user())

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_designate_challenger_requires_existing_champion():
    from fastapi import HTTPException
    from routers.ml import designate_challenger

    model = _make_model(model_id="model-b", status="STAGING", is_challenger=False)
    results = []

    find_result = MagicMock()
    find_result.scalar_one_or_none = MagicMock(return_value=model)
    results.append(find_result)

    champion_result = MagicMock()
    champion_result.scalar_one_or_none = MagicMock(return_value=None)
    results.append(champion_result)

    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()

    call_count = [0]

    async def execute_side_effect(stmt):
        idx = call_count[0]
        call_count[0] += 1
        return results[idx]

    db.execute = execute_side_effect

    with pytest.raises(HTTPException) as exc_info:
        await designate_challenger("model-b", db=db, current_user=_make_user())

    assert exc_info.value.status_code == 409


# ── ModelRegistryOut schema ───────────────────────────────────────────────────

def test_model_registry_out_optional_fields():
    """model_name and algorithm are Optional — rows without them must serialise."""
    from libs.schemas import ModelRegistryOut
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    out = ModelRegistryOut(
        id="abc",
        tenant_id="t1",
        model_name=None,
        algorithm=None,
        model_type="ANOMALY",
        version="20260314",
        metrics={"f1_score": 0.80},
        status="STAGING",
        is_challenger=False,
        created_at=now,
    )
    assert out.model_name is None
    assert out.algorithm is None
    assert out.status == "STAGING"


def test_model_registry_out_with_all_fields():
    """When all fields present, serialises without error."""
    from libs.schemas import ModelRegistryOut
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    out = ModelRegistryOut(
        id="abc",
        tenant_id="t1",
        model_name="IsolationForest",
        algorithm="IsolationForest",
        model_type="ANOMALY",
        version="20260314",
        training_rows=500,
        feature_columns=["deposit_sum_24h"],
        metrics={"f1_score": 0.85, "precision": 0.87, "recall": 0.83, "auc_roc": 0.90},
        status="champion",
        is_challenger=False,
        created_at=now,
        trained_at=now,
    )
    assert out.model_name == "IsolationForest"
    assert out.metrics["auc_roc"] == 0.90


def test_build_performance_summary_aggregates_feedback_by_model_and_rule():
    from routers.ml import _build_performance_summary

    now = datetime.now(UTC)
    model = SimpleNamespace(
        id="model-1",
        model_name="StructuringDetector",
        algorithm="GradientBoosting",
        status="champion",
    )
    alerts = [
        SimpleNamespace(
            id="a1",
            title="Structuring Spike — p1",
            rule_id="rule-1",
            label="TRUE_POSITIVE",
            created_at=now - timedelta(days=1),
            evidence={"model_id": "model-1"},
        ),
        SimpleNamespace(
            id="a2",
            title="Structuring Spike — p2",
            rule_id="rule-1",
            label="FALSE_POSITIVE",
            created_at=now - timedelta(days=1),
            evidence={"model_id": "model-1"},
        ),
        SimpleNamespace(
            id="a3",
            title="Structuring Spike — p3",
            rule_id="rule-1",
            label=None,
            created_at=now,
            evidence={"model_id": "model-1"},
        ),
    ]

    result = _build_performance_summary([model], alerts, days=30, challenger_split_pct=20)

    assert result["days_window"] == 30
    assert result["challenger_split_pct"] == 20
    assert result["totals"]["total_alerts"] == 3
    assert result["totals"]["labeled_alerts"] == 2
    assert result["totals"]["precision_estimated"] == 0.5
    assert result["by_rule"][0]["rule_name"] == "Structuring Spike"
    assert result["by_model"][0]["model_id"] == "model-1"
    assert result["by_model"][0]["false_positive_rate"] == 0.5


def test_build_ab_metrics_compares_champion_and_challenger():
    from routers.ml import _build_ab_metrics

    now = datetime.now(UTC)
    champion = SimpleNamespace(
        id="champ-1",
        model_name="IsolationForest",
        status="champion",
        is_challenger=False,
    )
    challenger = SimpleNamespace(
        id="chal-1",
        model_name="IsolationForest-v2",
        status="challenger",
        is_challenger=True,
    )
    logs = [
        SimpleNamespace(model_id="champ-1", anomaly_score=0.32, created_at=now - timedelta(days=1)),
        SimpleNamespace(model_id="chal-1", anomaly_score=0.61, created_at=now - timedelta(days=1)),
        SimpleNamespace(model_id="chal-1", anomaly_score=0.82, created_at=now),
    ]
    alerts = [
        SimpleNamespace(label="TRUE_POSITIVE", created_at=now - timedelta(days=1), evidence={"model_id": "champ-1"}),
        SimpleNamespace(label="FALSE_POSITIVE", created_at=now, evidence={"model_id": "chal-1"}),
        SimpleNamespace(label="TRUE_POSITIVE", created_at=now, evidence={"model_id": "chal-1"}),
    ]

    result = _build_ab_metrics(challenger, champion, logs, alerts, days=14)

    assert result["role"] == "challenger"
    assert result["champion_inferences"] == 1
    assert result["challenger_inferences"] == 2
    assert result["champion_precision_estimated"] == 1.0
    assert result["challenger_precision_estimated"] == 0.5
    assert len(result["timeline"]) == 2
