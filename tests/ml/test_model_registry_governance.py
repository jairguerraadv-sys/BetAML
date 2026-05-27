from __future__ import annotations

import importlib.util
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from libs.ml_governance import blocks_synthetic_model_promotion, is_synthetic_model


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ML_MAIN = os.path.join(ROOT, "services", "ml_service", "main.py")
API_DIR = os.path.join(ROOT, "services", "api")

if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)


def _load_ml_service_module():
    spec = importlib.util.spec_from_file_location("ml_service_main_governance", ML_MAIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ml_service_main_governance"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_blocks_synthetic_model_promotion_by_environment() -> None:
    assert blocks_synthetic_model_promotion("production") is True
    assert blocks_synthetic_model_promotion("staging") is True
    assert blocks_synthetic_model_promotion("development") is False
    assert blocks_synthetic_model_promotion("test") is False
    assert blocks_synthetic_model_promotion("local") is False


@pytest.mark.parametrize(
    "metrics",
    [
        {"synthetic_bootstrap": True},
        {"synthetic_bootstrap": "true"},
        {"synthetic_bootstrap": "1"},
        {"synthetic_bootstrap": "yes"},
    ],
)
def test_is_synthetic_model_truthy_variants(metrics: dict) -> None:
    assert is_synthetic_model(metrics, None) is True


@pytest.mark.parametrize(
    "metrics",
    [
        {"synthetic_bootstrap": False},
        {"synthetic_bootstrap": "false"},
        {},
        None,
    ],
)
def test_is_synthetic_model_falsy_variants(metrics: dict | None) -> None:
    assert is_synthetic_model(metrics, None) is False


def test_is_synthetic_model_explicit_flag_precedence() -> None:
    assert is_synthetic_model({}, True) is True
    # Explicit false does not mask legacy marker (backward compatibility)
    assert is_synthetic_model({"synthetic_bootstrap": True}, False) is True


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))


class _FakeBegin:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConn()
        self.begin_called = 0

    def begin(self):
        self.begin_called += 1
        return _FakeBegin(self.conn)


def test_register_model_db_blocks_synthetic_active_in_production() -> None:
    ml = _load_ml_service_module()
    engine = _FakeEngine()

    with patch.object(ml, "ENVIRONMENT", "production"):
        with pytest.raises(HTTPException) as exc:
            ml.register_model_db(
                engine,
                tenant_id="tenant-1",
                model_id="model-1",
                artifact_uri="s3://bucket/model.pkl",
                algorithm="IsolationForest",
                metrics={"training_rows": 100, "synthetic_bootstrap": True},
                feature_columns=["f1"],
                status="champion",
                is_active=True,
            )

    assert exc.value.status_code == 422
    assert engine.begin_called == 0


def test_register_model_db_blocks_synthetic_active_in_staging() -> None:
    ml = _load_ml_service_module()
    engine = _FakeEngine()

    with patch.object(ml, "ENVIRONMENT", "staging"):
        with pytest.raises(HTTPException):
            ml.register_model_db(
                engine,
                tenant_id="tenant-1",
                model_id="model-2",
                artifact_uri="s3://bucket/model.pkl",
                algorithm="IsolationForest",
                metrics={"training_rows": 100, "synthetic_bootstrap": "yes"},
                feature_columns=["f1"],
                status="champion",
                is_active=True,
            )

    assert engine.begin_called == 0


def test_register_model_db_allows_synthetic_active_in_development() -> None:
    ml = _load_ml_service_module()
    engine = _FakeEngine()

    with patch.object(ml, "ENVIRONMENT", "development"):
        ml.register_model_db(
            engine,
            tenant_id="tenant-1",
            model_id="model-3",
            artifact_uri="s3://bucket/model.pkl",
            algorithm="IsolationForest",
            metrics={"training_rows": 100, "synthetic_bootstrap": True},
            feature_columns=["f1"],
            status="champion",
            is_active=True,
        )

    assert engine.begin_called == 1
    assert any("trained_on_synthetic" in sql for sql, _ in engine.conn.executed)


def test_register_model_db_allows_non_synthetic_active_in_production() -> None:
    ml = _load_ml_service_module()
    engine = _FakeEngine()

    with patch.object(ml, "ENVIRONMENT", "production"):
        ml.register_model_db(
            engine,
            tenant_id="tenant-1",
            model_id="model-4",
            artifact_uri="s3://bucket/model.pkl",
            algorithm="IsolationForest",
            metrics={"training_rows": 100, "synthetic_bootstrap": False},
            feature_columns=["f1"],
            status="champion",
            is_active=True,
        )

    assert engine.begin_called == 1


@pytest.mark.asyncio
async def test_promote_model_blocks_synthetic_candidate_in_production() -> None:
    from routers.ml import promote_model

    model = SimpleNamespace(
        id="model-x",
        tenant_id="tenant-1",
        status="challenger",
        is_challenger=True,
        is_active=False,
        model_type="ANOMALY",
        metrics={"synthetic_bootstrap": True},
        trained_on_synthetic=True,
    )
    current_user = SimpleNamespace(id="user-1", tenant_id="tenant-1")

    db = AsyncMock()
    find_result = MagicMock()
    find_result.scalar_one_or_none.return_value = model
    db.execute = AsyncMock(return_value=find_result)
    db.commit = AsyncMock()

    with patch("routers.ml.settings.environment", "production"), patch(
        "routers.ml._write_audit", new_callable=AsyncMock
    ) as audit_mock:
        with pytest.raises(HTTPException) as exc:
            await promote_model("model-x", db=db, current_user=current_user)

    assert exc.value.status_code == 422
    assert model.status == "challenger"
    assert model.is_active is False
    # only SELECT by id, no archive update query
    assert db.execute.await_count == 1
    audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_promote_model_allows_non_synthetic_in_production() -> None:
    from routers.ml import promote_model

    model = SimpleNamespace(
        id="model-y",
        tenant_id="tenant-1",
        status="challenger",
        is_challenger=True,
        is_active=False,
        model_type="ANOMALY",
        metrics={"synthetic_bootstrap": False},
        trained_on_synthetic=False,
        champion_id="old",
        promoted_by=None,
        promoted_at=None,
    )
    current_user = SimpleNamespace(id="user-1", tenant_id="tenant-1")

    db = AsyncMock()
    find_result = MagicMock()
    find_result.scalar_one_or_none.return_value = model
    db.execute = AsyncMock(side_effect=[find_result, MagicMock()])
    db.commit = AsyncMock()

    with patch("routers.ml.settings.environment", "production"), patch(
        "routers.ml._write_audit", new_callable=AsyncMock
    ):
        response = await promote_model("model-y", db=db, current_user=current_user)

    assert response["status"] == "promoted"
    assert model.status == "champion"
    assert model.is_active is True
