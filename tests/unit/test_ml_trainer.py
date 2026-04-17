"""
tests/unit/test_ml_trainer.py — Unit tests for the ml_trainer service.

Tests cover:
  - TestMLTrainerInsufficientData: returns early (before commit / MinIO put) when
    there are fewer than 50 extractable feature vectors in the unsupervised path.
  - TestMLTrainerSupervisedPath: uses GradientBoostingClassifier when >= 50 labeled
    alerts exist; IsolationForest is used (and GradientBoosting skipped) otherwise.
  - TestMLTrainerNoChampion: promotes the new model (status="champion") when no
    current champion exists, provided F1 > 0.75.
  - TestMLTrainerPromotion: promotes new model AND archives old champion when
    F1 > 0.75 and precision does not regress by more than 5%.
  - TestMLTrainerRegression: blocks promotion (status="STAGING", old champion NOT
    archived) when new precision < 95% of champion precision.

Patching strategy
-----------------
  main.Session              — async_sessionmaker created at module level in main.py
  sys.modules["models"]     — lazy `from models import ...` inside the function
                              patched to avoid SQLAlchemy column-descriptor errors
  sys.modules["minio"]      — `from minio import Minio` inside the function;
                              minio may not be installed in the dev test environment
  main.select               — avoids passing MagicMock ORM classes to the real
                              SQLAlchemy select() which would raise TypeError
  main.GradientBoostingClassifier / main.IsolationForest — avoids actual training
  main.f1_score / precision_score / recall_score / roc_auc_score — controlled floats
  main.pickle.dumps         — MagicMock models are not picklable; returns b"fake"
"""
from __future__ import annotations

import os
import sys
import types
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ── Ensure services/ml_trainer is importable ──────────────────────────────────
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ml_trainer_path = os.path.join(_root, "services", "ml_trainer")
if _ml_trainer_path not in sys.path:
    sys.path.insert(0, _ml_trainer_path)

# 24 feature columns mirrored from ml_trainer/main.py (used to build test fixtures)
FEATURE_COLUMNS = [
    "deposit_sum_24h", "deposit_sum_7d", "deposit_count_7d",
    "withdrawal_sum_24h", "withdrawal_sum_7d", "cashout_ratio_30d",
    "velocity_score", "night_activity_ratio", "round_amount_ratio",
    "avg_bet_stake", "bet_count_7d", "win_loss_ratio_30d",
    "structuring_score", "layering_score", "rapid_cashout_score",
    "pep_flag", "account_age_days", "login_count_7d",
    "unique_payment_methods_30d", "deposit_withdrawal_gap_hours",
    "high_risk_events_count", "network_centrality_score",
    "ml_anomaly_score", "composite_risk_score",
]


# ─────────────────────────────────────────────────────────────────────────────
# Fixture builders
# ─────────────────────────────────────────────────────────────────────────────

def _feature_dict(value: float = 0.5) -> dict:
    """Return a full 24-column feature dict (all columns present)."""
    return {col: value for col in FEATURE_COLUMNS}


def _labeled_alert(label: str = "TRUE_POSITIVE") -> MagicMock:
    a = MagicMock()
    a.label = label
    a.evidence = {"features": _feature_dict()}
    return a


def _unlabeled_alert() -> MagicMock:
    a = MagicMock()
    a.label = None
    a.evidence = {"features": _feature_dict(0.3)}
    return a


def _empty_alert() -> MagicMock:
    """Alert with no features key in evidence — will be filtered out."""
    a = MagicMock()
    a.label = None
    a.evidence = {}
    return a


def _champion(precision: float = 0.90) -> MagicMock:
    c = MagicMock()
    c.is_champion = True
    c.metrics = {"precision": precision, "recall": 0.85, "f1_score": 0.87}
    c.trained_at = datetime.now(UTC) - timedelta(days=7)
    return c


def _admin(tenant_id: str = "tenant-1", user_id: str = "user-1") -> MagicMock:
    u = MagicMock()
    u.tenant_id = tenant_id
    u.id = user_id
    return u


class _SQLColMock:
    """
    Plain Python class that mimics a SQLAlchemy column object for WHERE clauses.

    Python 3.12 changed MagicMock's comparison dunder methods (__ge__, __le__, …)
    to return `NotImplemented`, which causes `Alert.created_at >= datetime_obj` to
    raise TypeError (datetime.__le__(MagicMock) also returns NotImplemented).

    Using a plain, non-MagicMock class bypasses the metaclass magic that overwrites
    dunder methods, letting the comparison operators return a truthy placeholder.
    """

    def __ge__(self, other):  # noqa: D105
        return _SQLColMock()

    def __le__(self, other):  # noqa: D105
        return _SQLColMock()

    def __gt__(self, other):  # noqa: D105
        return _SQLColMock()

    def __lt__(self, other):  # noqa: D105
        return _SQLColMock()

    def __getattr__(self, name: str) -> "_SQLColMock":  # covers .in_(), .is_(), etc.
        return _SQLColMock()

    def __call__(self, *args, **kwargs) -> "_SQLColMock":
        return _SQLColMock()

    def __bool__(self) -> bool:
        return True


def _mock_models() -> types.ModuleType:
    """
    Build a lightweight mock 'models' module whose classes behave as MagicMocks.

    The lazy import inside retrain_isolation_forest() does
      from models import Alert, ModelRegistry, Notification, User
    Replacing sys.modules["models"] with this object ensures:
      - ModelRegistry.status  → MagicMock (no AttributeError)
      - ModelRegistry(status="champion", ...)  → call is recorded; kwargs inspectable
      - Notification(...)  → each call creates a distinct mock instance
    """
    mod = types.ModuleType("models")
    mod.Alert = MagicMock(name="Alert")
    # Python 3.12: MagicMock comparison operators return NotImplemented.
    # Alert.created_at is used with >= in queries — use a plain _SQLColMock.
    mod.Alert.created_at = _SQLColMock()
    mod.ModelRegistry = MagicMock(name="ModelRegistry")
    # side_effect so every Notification(...) call returns a *unique* MagicMock instance
    mod.Notification = MagicMock(
        name="Notification",
        side_effect=lambda **_kw: MagicMock(),
    )
    mod.User = MagicMock(name="User")
    return mod


def _mock_minio() -> tuple[types.ModuleType, MagicMock]:
    """Return (mock_minio_module, mock_minio_instance)."""
    inst = MagicMock(name="MinioInstance")
    inst.bucket_exists.return_value = True
    inst.put_object = MagicMock()
    mod = types.ModuleType("minio")
    mod.Minio = MagicMock(name="Minio", return_value=inst)
    return mod, inst


def _session(execute_configurators: list) -> tuple[MagicMock, AsyncMock]:
    """
    Build (session_factory, session) where every db.execute() call invokes
    the matching configurator from execute_configurators (indexed by call order).

    Each configurator is a callable that receives the MagicMock result object and
    sets up the return value (e.g. result.scalars().all() or scalar_one_or_none()).
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()

    idx = [0]

    async def _execute(stmt):  # noqa: ARG001
        result = MagicMock()
        i = idx[0]
        idx[0] += 1
        if i < len(execute_configurators):
            execute_configurators[i](result)
        return result

    session.execute = _execute
    factory = MagicMock(return_value=session)
    return factory, session


# ─────────────────────────────────────────────────────────────────────────────
# Shared patch helper (ExitStack-based to keep tests DRY)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_patches(
    stack: ExitStack,
    *,
    session_factory: MagicMock,
    mock_models_mod: types.ModuleType,
    mock_minio_mod: types.ModuleType,
    gb_instance: MagicMock | None = None,
    if_instance: MagicMock | None = None,
    registry_tenant_id: str | None = "tenant-1",
    f1: float = 0.80,
    precision: float = 0.82,
    recall: float = 0.78,
) -> dict:
    """
    Enter all standard patches into *stack* and return a mapping of patch targets
    to their mock objects for assertions.

    Patches applied:
      sys.modules["models"]          ← mock_models_mod
      sys.modules["minio"]           ← mock_minio_mod
      main.Session                   ← session_factory
      main.select                    ← MagicMock (no-op query construction)
      main.GradientBoostingClassifier ← gb_instance (or bare MagicMock)
      main.IsolationForest           ← if_instance (or bare MagicMock)
      main.f1_score / precision_score / recall_score  ← controlled floats
      main.pickle.dumps              ← returns b"fake_model_bytes"
    """
    # ── Ensure services/ml_trainer is first in sys.path ──────────────────────
    # tests/unit/conftest.py puts services/api at index 0 at session start,
    # overriding this module's sys.path.insert(0, _ml_trainer_path) which ran
    # at collection time.  We temporarily re-prepend ml_trainer and evict any
    # cached 'main' (= services/api/main.py) so that `patch("main.*")` and
    # `from main import ...` both resolve to services/ml_trainer/main.py.
    _stale_main = sys.modules.pop("main", None)

    def _restore_main() -> None:
        sys.modules.pop("main", None)
        if _stale_main is not None:
            sys.modules["main"] = _stale_main

    stack.callback(_restore_main)

    if not sys.path or sys.path[0] != _ml_trainer_path:
        sys.path.insert(0, _ml_trainer_path)

        def _remove_ml_path() -> None:
            try:
                sys.path.remove(_ml_trainer_path)
            except ValueError:
                pass

        stack.callback(_remove_ml_path)

    # Install models/minio mocks BEFORE 'main' is imported (the lazy
    # `from models import ...` inside retrain_isolation_forest must see
    # the mock, not the real SQLAlchemy-backed models module).
    stack.enter_context(
        patch.dict(
            sys.modules,
            {"models": mock_models_mod, "minio": mock_minio_mod},
        )
    )
    stack.enter_context(patch("main.Session", session_factory))
    stack.enter_context(patch("main.select"))

    gb_mock = stack.enter_context(
        patch(
            "main.GradientBoostingClassifier",
            return_value=gb_instance if gb_instance is not None else MagicMock(),
        )
    )
    if_mock = stack.enter_context(
        patch(
            "main.IsolationForest",
            return_value=if_instance if if_instance is not None else MagicMock(),
        )
    )

    stack.enter_context(patch("main.f1_score", return_value=f1))
    stack.enter_context(patch("main.precision_score", return_value=precision))
    stack.enter_context(patch("main.recall_score", return_value=recall))
    stack.enter_context(patch("main.roc_auc_score", return_value=0.85))
    stack.enter_context(patch("main.pickle.dumps", return_value=b"fake_model_bytes"))
    stack.enter_context(
        patch(
            "main._resolve_registry_tenant_id",
            new=AsyncMock(return_value=registry_tenant_id),
        )
    )

    return {"gb_cls": gb_mock, "if_cls": if_mock}


# ─────────────────────────────────────────────────────────────────────────────
# TestMLTrainerInsufficientData
# ─────────────────────────────────────────────────────────────────────────────

class TestMLTrainerInsufficientData:
    """
    The unsupervised (IsolationForest) fallback path queries ALL recent alerts
    and returns early when the total number of extractable feature vectors is < 50.
    In that case no model is persisted to MinIO and session.commit is never called.
    """

    @pytest.mark.asyncio
    async def test_returns_early_when_no_alerts_exist(self):
        """Zero labeled + zero total alerts → unsupervised path → early return."""
        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = []

        def cfg_all(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_all])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        session.commit.assert_not_called()
        minio_inst.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_fewer_than_50_total_feature_vectors(self):
        """
        Labeled < 50 (unsupervised path) AND total alerts have only 30 valid
        feature vectors — below the 50-vector threshold → early return.
        """
        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = []

        def cfg_all(r):
            r.scalars.return_value.all.return_value = [
                _unlabeled_alert() for _ in range(30)
            ]

        factory, session = _session([cfg_labeled, cfg_all])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        session.commit.assert_not_called()
        minio_inst.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_all_alerts_have_empty_evidence(self):
        """
        100 total alerts but none have a 'features' key in evidence →
        zero feature vectors extracted → early return.
        """
        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = []

        def cfg_all(r):
            r.scalars.return_value.all.return_value = [
                _empty_alert() for _ in range(100)
            ]

        factory, session = _session([cfg_labeled, cfg_all])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        session.commit.assert_not_called()
        minio_inst.put_object.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# TestMLTrainerSupervisedPath
# ─────────────────────────────────────────────────────────────────────────────

class TestMLTrainerSupervisedPath:
    """
    When >= 50 labeled alerts with valid feature vectors exist, the job must use
    GradientBoostingClassifier (supervised mode) and skip the IsolationForest path.
    Conversely, when < 50 labeled but >= 50 total alerts exist, IsolationForest
    is used and GradientBoosting is skipped.
    """

    @pytest.mark.asyncio
    async def test_gradient_boosting_used_when_50_plus_labeled(self):
        """55 labeled alerts → GradientBoostingClassifier trained, IsolationForest skipped."""
        labeled = (
            [_labeled_alert("TRUE_POSITIVE")] * 40
            + [_labeled_alert("FALSE_POSITIVE")] * 15
        )

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            mocks = _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.82,
                precision=0.88,
                recall=0.76,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        mocks["gb_cls"].assert_called_once()
        gb_inst.fit.assert_called_once()
        mocks["if_cls"].assert_not_called()
        session.commit.assert_called_once()
        assert minio_inst.put_object.call_count >= 1

    @pytest.mark.asyncio
    async def test_gradient_boosting_model_filename_in_minio(self):
        """MinIO object_name must contain 'gradient_boosting' in supervised mode."""
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=0.85,
                recall=0.75,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        object_names = [
            c.kwargs.get("object_name", "")
            for c in minio_inst.put_object.call_args_list
        ]
        assert any("gradient_boosting" in name for name in object_names)

    @pytest.mark.asyncio
    async def test_isolation_forest_used_when_fewer_than_50_labeled(self):
        """
        < 50 labeled alerts (unsupervised path) but >= 50 total alerts with features →
        IsolationForest trained, GradientBoosting skipped.
        """
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 10  # < 50 → unsupervised
        all_alerts = [_unlabeled_alert() for _ in range(60)]

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_all(r):
            r.scalars.return_value.all.return_value = all_alerts

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_all, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        if_inst = MagicMock()
        if_inst.predict.side_effect = lambda X: np.full(len(X), -1)

        with ExitStack() as stack:
            mocks = _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                if_instance=if_inst,
                f1=0.80,
                precision=0.82,
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        mocks["if_cls"].assert_called_once()
        if_inst.fit.assert_called_once()
        mocks["gb_cls"].assert_not_called()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_isolation_forest_model_filename_in_minio(self):
        """MinIO object_name must contain 'isolation_forest' in unsupervised mode."""
        labeled = []
        all_alerts = [_unlabeled_alert() for _ in range(60)]

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_all(r):
            r.scalars.return_value.all.return_value = all_alerts

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_all, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        if_inst = MagicMock()
        if_inst.predict.side_effect = lambda X: np.full(len(X), -1)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                if_instance=if_inst,
                f1=0.80,
                precision=0.82,
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        object_names = [
            c.kwargs.get("object_name", "")
            for c in minio_inst.put_object.call_args_list
        ]
        assert any("isolation_forest" in name for name in object_names)

    @pytest.mark.asyncio
    async def test_unsupervised_model_stays_staging_even_with_high_f1(self):
        labeled = []
        all_alerts = [_unlabeled_alert() for _ in range(60)]

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_all(r):
            r.scalars.return_value.all.return_value = all_alerts

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_all, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        if_inst = MagicMock()
        if_inst.predict.side_effect = lambda X: np.full(len(X), -1)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                if_instance=if_inst,
                f1=0.95,
                precision=0.95,
                recall=0.95,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "STAGING"


# ─────────────────────────────────────────────────────────────────────────────
# TestMLTrainerNoChampion
# ─────────────────────────────────────────────────────────────────────────────

class TestMLTrainerNoChampion:
    """
    When no current champion exists, champion_precision = 0.0 so the precision
    regression check is always False.  The new model is promoted (status="champion")
    iff F1 > 0.75; no de-promotion call is made on any existing champion.
    """

    @pytest.mark.asyncio
    async def test_promotes_when_no_champion_and_f1_above_threshold(self):
        """No current champion + F1=0.80 > 0.75 → ModelRegistry created with status='champion'."""
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None  # no current champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=0.82,
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # ModelRegistry was called with status="champion"
        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "champion"
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_promotion_when_f1_below_threshold_and_no_champion(self):
        """No current champion + F1=0.70 < 0.75 → ModelRegistry created with status='STAGING'."""
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.70,  # below threshold
                precision=0.75,
                recall=0.65,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "STAGING"

    @pytest.mark.asyncio
    async def test_no_depromote_call_on_session_when_no_champion_exists(self):
        """
        When current_champion is None, the code must NOT call db.add(current_champion)
        (there is nothing to de-promote).  Only the new registry entry is added.
        """
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = None

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        added_objects: list = []
        session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=0.82,
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # With no admins and no champion to de-promote, add() is called only once
        # (for the registry entry).  None of the added objects should be a MagicMock
        # champion (no champion was returned from the DB query).
        registry_entry = mock_models_mod.ModelRegistry.return_value
        assert registry_entry in added_objects, "New registry entry must be added to session"
        # Confirm only the registry entry was added (no champion mock, no notifications)
        assert added_objects == [registry_entry]


# ─────────────────────────────────────────────────────────────────────────────
# TestMLTrainerPromotion
# ─────────────────────────────────────────────────────────────────────────────

class TestMLTrainerPromotion:
    """
    When a current champion exists AND the new model meets both criteria
    (F1 > 0.75 AND precision >= 95% of champion precision):
      - The old champion's status attribute is set to "archived"
      - The old champion object is passed to db.add() so the change is persisted
      - The new ModelRegistry entry has status="champion"
    """

    @pytest.mark.asyncio
    async def test_promotes_new_model_and_depromotes_old_champion(self):
        """
        Champion precision=0.85.  New precision=0.82 >= 0.85*0.95=0.8075.  F1=0.80.
        Expected: old champion.status set to "archived"; new entry status="champion".
        """
        old_champion = _champion(precision=0.85)
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = [_admin("t1", "u1")]

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        added_objects: list = []
        session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=0.82,   # 0.82 >= 0.85 * 0.95 = 0.8075 → not a regression
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # Old champion was archived (attribute set to "archived")
        assert old_champion.status == "archived"

        # Old champion was added to the session to persist the status change
        assert old_champion in added_objects

        # New registry entry is champion
        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "champion"

        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_admin_notifications_sent_for_each_admin_on_promotion(self):
        """On successful promotion, one Notification is created per ADMIN user."""
        old_champion = _champion(precision=0.80)
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50
        admin1 = _admin("t1", "u1")
        admin2 = _admin("t2", "u2")

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = [admin1, admin2]

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=0.82,   # 0.82 >= 0.80 * 0.95 = 0.76 → OK
                recall=0.78,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # Notification constructor called exactly twice (once per admin)
        assert mock_models_mod.Notification.call_count == 2

        # Each notification targets the right user
        notif_calls = mock_models_mod.Notification.call_args_list
        user_ids = {c.kwargs.get("user_id") for c in notif_calls}
        assert "u1" in user_ids
        assert "u2" in user_ids

        # Each notification has the correct type
        for c in notif_calls:
            assert c.kwargs.get("type") == "ML_TRAINING_COMPLETED"


# ─────────────────────────────────────────────────────────────────────────────
# TestMLTrainerRegression
# ─────────────────────────────────────────────────────────────────────────────

class TestMLTrainerRegression:
    """
    The promotion guard:  precision_regression = (champion_precision > 0) AND
    (new_precision < champion_precision * 0.95).
    When True: status="STAGING" and the old champion is NOT archived.
    The model is still persisted to MinIO and registered as a non-champion entry.
    """

    @pytest.mark.asyncio
    async def test_precision_regression_blocks_promotion(self):
        """
        Champion precision=0.90.  New precision=0.80 < 0.90*0.95=0.855.  F1=0.85.
        Expected: status="STAGING", old champion.status remains unchanged.
        """
        old_champion = _champion(precision=0.90)
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.85,        # > 0.75 (would pass f1 check)
                precision=0.80,  # 0.80 < 0.90 * 0.95 = 0.855 → REGRESSION
                recall=0.90,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # New registry entry must NOT be champion
        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "STAGING"

        # Old champion status must NOT have been set to "archived"
        assert old_champion.status != "archived"

        # Old champion must NOT have been added to session (no archiving)
        added_args = [c.args[0] for c in session.add.call_args_list]
        assert old_champion not in added_args

    @pytest.mark.asyncio
    async def test_precision_regression_model_still_persisted_and_committed(self):
        """
        Even when promotion is blocked by precision regression, the model artifact
        must still be saved to MinIO and committed to model_registry as non-champion.
        """
        old_champion = _champion(precision=0.95)
        labeled = [_labeled_alert("FALSE_POSITIVE")] * 60

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = [_admin()]

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, minio_inst = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.85,
                precision=0.70,   # 0.70 < 0.95 * 0.95 = 0.9025 → REGRESSION
                recall=0.90,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        assert minio_inst.put_object.call_count >= 1
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_regression_notification_body_mentions_precision_drop(self):
        """
        When promotion is blocked by regression, the notification body must
        describe the precision regression (not promote language).
        """
        old_champion = _champion(precision=0.90)
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = [_admin()]

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.85,
                precision=0.80,   # regression
                recall=0.90,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        notif_call = mock_models_mod.Notification.call_args
        body = notif_call.kwargs.get("body", "")
        # Body must reference the precision regression, not a promotion
        assert "regressão" in body or "Bloqueado" in body or "bloqueado" in body.lower()

    @pytest.mark.asyncio
    async def test_boundary_precision_at_exactly_95_percent_is_not_regression(self):
        """
        Boundary condition: new_precision == champion_precision * 0.95 (strictly equal).
        The guard uses strict < so exactly 95% is NOT a regression.
        Expected: is_champion=True (F1 check also passes), old champion de-promoted.
        """
        champion_precision = 0.90
        new_precision = champion_precision * 0.95  # exactly 0.855 — not a regression

        old_champion = _champion(precision=champion_precision)
        labeled = [_labeled_alert("TRUE_POSITIVE")] * 50

        def cfg_labeled(r):
            r.scalars.return_value.all.return_value = labeled

        def cfg_champion(r):
            r.scalar_one_or_none.return_value = old_champion

        def cfg_admins(r):
            r.scalars.return_value.all.return_value = []

        factory, session = _session([cfg_labeled, cfg_champion, cfg_admins])
        mock_models_mod = _mock_models()
        mock_minio_mod, _ = _mock_minio()

        gb_inst = MagicMock()
        gb_inst.predict.side_effect = lambda X: np.ones(len(X), dtype=int)

        with ExitStack() as stack:
            _apply_patches(
                stack,
                session_factory=factory,
                mock_models_mod=mock_models_mod,
                mock_minio_mod=mock_minio_mod,
                gb_instance=gb_inst,
                f1=0.80,
                precision=new_precision,  # exactly at boundary → not a regression
                recall=0.80,
            )
            from main import retrain_isolation_forest
            await retrain_isolation_forest()

        # Boundary: not a regression → should be promoted
        registry_call_kwargs = mock_models_mod.ModelRegistry.call_args.kwargs
        assert registry_call_kwargs.get("status") == "champion"

        # Old champion archived
        assert old_champion.status == "archived"
