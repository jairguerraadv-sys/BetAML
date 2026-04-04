"""
Testes unitários do ML Service — BetAML.

Testa train() e as estruturas de predicção sem infraestrutura real.
MinIO e Postgres são mockados para isolar a lógica de ML.

Cobre:
  - train() com dados sintéticos (ClickHouse indisponível → fallback)
  - TrainResponse tem campos obrigatórios e valores válidos
  - IsolationForest treina com o número correto de linhas
  - FEATURE_COLS contém os campos esperados
  - _features_to_vector() converte dict corretamente
  - _features_to_vector() preenche 0.0 em campos ausentes
  - Modelo treinado in-memory é inferível (predict retorna ±1)
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

# ── Path setup ───────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ML_MAIN = os.path.join(_ROOT, "services", "ml_service", "main.py")

# Garante que libs e ml_service config estão no path
sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "ml_service"))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_ML_MODULE = None


def _load_module():
    """
    Carrega ml_service/main.py com nome de módulo único para evitar conflito
    com sys.modules["main"] que pode já estar ocupado pelo stream_processor.
    O módulo é cacheado para garantir que TrainResponse seja sempre a mesma classe.
    """
    global _ML_MODULE
    if _ML_MODULE is not None:
        return _ML_MODULE
    spec = importlib.util.spec_from_file_location("ml_service_main", _ML_MAIN)
    ml = importlib.util.module_from_spec(spec)
    sys.modules["ml_service_main"] = ml
    spec.loader.exec_module(ml)  # type: ignore[union-attr]
    _ML_MODULE = ml
    return ml


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureCols(unittest.TestCase):
    """FEATURE_COLS deve conter os campos de v1 e v2."""

    def test_v1_fields_present(self):
        ml = _load_module()
        v1 = {"deposit_sum_24h", "deposit_sum_7d", "withdrawal_sum_24h",
               "withdrawal_sum_7d", "deposit_count_24h", "shared_device_count"}
        self.assertTrue(v1.issubset(set(ml.FEATURE_COLS)))

    def test_v2_fields_present(self):
        ml = _load_module()
        v2 = {"deposit_velocity", "night_activity_ratio", "weekend_activity_ratio",
               "chargeback_rate_30d", "cashout_ratio_7d"}
        self.assertTrue(v2.issubset(set(ml.FEATURE_COLS)))

    def test_no_duplicate_cols(self):
        ml = _load_module()
        self.assertEqual(len(ml.FEATURE_COLS), len(set(ml.FEATURE_COLS)))


class TestFeaturesVector(unittest.TestCase):
    """_features_to_vector() deve converter dict → np.ndarray corretamente."""

    def test_full_dict_converts(self):
        import numpy as np
        ml = _load_module()
        feat = {col: float(i) for i, col in enumerate(ml.FEATURE_COLS)}
        vec = ml._features_to_vector(feat, ml.FEATURE_COLS)
        self.assertEqual(vec.shape, (1, len(ml.FEATURE_COLS)))
        self.assertEqual(vec.dtype, np.float32)

    def test_missing_keys_default_to_zero(self):
        import numpy as np
        ml = _load_module()
        vec = ml._features_to_vector({}, ml.FEATURE_COLS)
        self.assertEqual(vec.shape, (1, len(ml.FEATURE_COLS)))
        self.assertTrue((vec == 0.0).all())

    def test_none_values_default_to_zero(self):
        import numpy as np
        ml = _load_module()
        feat = {col: None for col in ml.FEATURE_COLS}
        vec = ml._features_to_vector(feat, ml.FEATURE_COLS)
        self.assertTrue((vec == 0.0).all())

    def test_numeric_string_converted(self):
        import numpy as np
        ml = _load_module()
        feat = {ml.FEATURE_COLS[0]: "42.5"}
        vec = ml._features_to_vector(feat, ml.FEATURE_COLS)
        self.assertAlmostEqual(float(vec[0, 0]), 42.5, places=4)

    def test_alias_fields_are_normalized(self):
        ml = _load_module()
        feat = {
            "unique_instruments_used_7d": 7,
            "bonus_to_real_money_ratio_30d": "0.25",
        }
        normalized = ml._normalize_feature_aliases(feat)
        self.assertEqual(normalized["unique_instruments_7d"], 7)
        self.assertEqual(normalized["bonus_to_real_ratio_30d"], "0.25")

    def test_vector_uses_alias_when_canonical_missing(self):
        ml = _load_module()
        feat = {
            "unique_instruments_used_7d": 9,
            "bonus_to_real_money_ratio_30d": 0.5,
        }
        vec = ml._features_to_vector(feat, ["unique_instruments_7d", "bonus_to_real_ratio_30d"])
        self.assertEqual(float(vec[0, 0]), 9.0)
        self.assertAlmostEqual(float(vec[0, 1]), 0.5, places=4)


class TestTrainSyntheticData(unittest.TestCase):
    """
    train() deve usar dados sintéticos quando ClickHouse está indisponível,
    e retornar um TrainResponse válido.
    """

    def _run_train(self, min_rows: int = 50):
        ml = _load_module()

        # Patch external deps so test is hermetic
        with patch.object(ml, "upload_model_artifact", return_value="memory://test/model.pkl"), \
             patch.object(ml, "register_model_db", return_value=None), \
             patch.object(ml, "_db_engine", return_value=MagicMock()):
            req = ml.TrainRequest(tenant_id="tenant-test", min_rows=min_rows)
            return ml.train(req)

    def test_returns_train_response(self):
        ml = _load_module()
        result = self._run_train()
        self.assertIsInstance(result, ml.TrainResponse)

    def test_model_id_is_uuid_string(self):
        import uuid
        result = self._run_train()
        # Should not raise
        uuid.UUID(result.model_id)

    def test_tenant_id_preserved(self):
        result = self._run_train()
        self.assertEqual(result.tenant_id, "tenant-test")

    def test_algorithm_is_isolation_forest(self):
        result = self._run_train()
        self.assertEqual(result.algorithm, "IsolationForest")

    def test_training_rows_at_least_min_rows(self):
        result = self._run_train(min_rows=50)
        # Synthetic data generates max(min_rows, 1000)
        self.assertGreaterEqual(result.training_rows, 50)

    def test_metrics_contain_required_keys(self):
        result = self._run_train()
        for key in ("training_rows", "n_estimators", "contamination", "train_secs"):
            self.assertIn(key, result.metrics, f"Chave ausente em metrics: {key}")

    def test_train_secs_is_positive(self):
        result = self._run_train()
        self.assertGreater(result.metrics["train_secs"], 0.0)


class TestTrainMinRowsConfig(unittest.TestCase):
    """train() respeita min_rows para geração sintética."""

    def test_custom_min_rows_reflected_in_response(self):
        ml = _load_module()
        with patch.object(ml, "upload_model_artifact", return_value="memory://t/m.pkl"), \
             patch.object(ml, "register_model_db", return_value=None), \
             patch.object(ml, "_db_engine", return_value=MagicMock()):
            req = ml.TrainRequest(tenant_id="tenant-x", min_rows=200)
            result = ml.train(req)
        # Synthetic data: max(200, 1000) = 1000
        self.assertGreaterEqual(result.training_rows, 200)


class TestTrainModelIsUsable(unittest.TestCase):
    """
    Verifica que o modelo treinado pode fazer predições (smoke test de pipeline).
    Usamos IsolationForest diretamente para não depender de MinIO/Postgres.
    """

    def test_isolation_forest_predict_returns_plus_minus_one(self):
        import numpy as np
        from sklearn.ensemble import IsolationForest

        ml = _load_module()

        rng = np.random.default_rng(0)
        X = rng.exponential(100, (500, len(ml.FEATURE_COLS))).astype(np.float32)
        clf = IsolationForest(n_estimators=10, contamination=0.05, random_state=42)
        clf.fit(X)
        preds = clf.predict(X[:10])
        self.assertTrue(set(preds).issubset({-1, 1}))


class TestTrainResponseSchema(unittest.TestCase):
    """TrainResponse e TrainRequest têm os campos esperados pelo schema."""

    def test_train_request_fields(self):
        ml = _load_module()
        req = ml.TrainRequest(tenant_id="t", min_rows=500)
        self.assertEqual(req.tenant_id, "t")
        self.assertEqual(req.min_rows, 500)

    def test_train_response_instantiation(self):
        ml = _load_module()
        resp = ml.TrainResponse(
            model_id="abc-123",
            tenant_id="t",
            algorithm="IsolationForest",
            training_rows=1000,
            metrics={"training_rows": 1000, "n_estimators": 200, "contamination": 0.05, "train_secs": 1.2},
        )
        self.assertEqual(resp.model_id, "abc-123")
        self.assertEqual(resp.training_rows, 1000)


class _DummyClf:
    def __init__(self, raw_score: float = 0.0):
        self._raw_score = raw_score

    def decision_function(self, X):
        import numpy as np
        return np.array([self._raw_score], dtype=np.float32)


class TestABTrafficSplit(unittest.TestCase):
    def test_choose_model_variant_is_deterministic(self):
        ml = _load_module()
        tid = "tenant-1"
        pid = "player-1"
        v1 = ml._choose_model_variant(tid, pid, 50)
        v2 = ml._choose_model_variant(tid, pid, 50)
        self.assertIn(v1, {"champion", "challenger"})
        self.assertEqual(v1, v2)

    def test_pct_zero_always_champion(self):
        ml = _load_module()
        req = ml.ScoreRequest(player_id="p1", tenant_id="t1", features={"deposit_sum_24h": 1})

        calls = []

        def _load_side_effect(tenant_id: str, model_type: str = "champion"):
            calls.append(model_type)
            return {"clf": _DummyClf(0.0), "model_id": "00000000-0000-0000-0000-000000000001"}

        with patch.object(ml, "_db_engine", return_value=MagicMock()), \
             patch.object(ml, "_get_ml_challenger_pct", return_value=0), \
             patch.object(ml, "_load_tenant_model", side_effect=_load_side_effect), \
             patch.object(ml, "_log_inference", return_value=None):
            resp = ml.score(req)

        self.assertEqual(resp.model_id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(calls, ["champion"])

    def test_pct_hundred_uses_challenger_when_available(self):
        ml = _load_module()
        req = ml.ScoreRequest(player_id="p1", tenant_id="t1", features={"deposit_sum_24h": 1})

        def _load_side_effect(tenant_id: str, model_type: str = "champion"):
            if model_type == "challenger":
                return {"clf": _DummyClf(0.0), "model_id": "00000000-0000-0000-0000-000000000002"}
            return {"clf": _DummyClf(0.0), "model_id": "00000000-0000-0000-0000-000000000001"}

        with patch.object(ml, "_db_engine", return_value=MagicMock()), \
             patch.object(ml, "_get_ml_challenger_pct", return_value=100), \
             patch.object(ml, "_load_tenant_model", side_effect=_load_side_effect), \
             patch.object(ml, "_log_inference", return_value=None):
            resp = ml.score(req)

        self.assertEqual(resp.model_id, "00000000-0000-0000-0000-000000000002")

    def test_pct_hundred_falls_back_to_champion_when_challenger_missing(self):
        ml = _load_module()
        req = ml.ScoreRequest(player_id="p1", tenant_id="t1", features={"deposit_sum_24h": 1})

        calls = []

        def _load_side_effect(tenant_id: str, model_type: str = "champion"):
            calls.append(model_type)
            if model_type == "challenger":
                return None
            return {"clf": _DummyClf(0.0), "model_id": "00000000-0000-0000-0000-000000000001"}

        with patch.object(ml, "_db_engine", return_value=MagicMock()), \
             patch.object(ml, "_get_ml_challenger_pct", return_value=100), \
             patch.object(ml, "_load_tenant_model", side_effect=_load_side_effect), \
             patch.object(ml, "_log_inference", return_value=None):
            resp = ml.score(req)

        self.assertEqual(resp.model_id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(calls, ["challenger", "champion"])

    def test_score_without_model_raises_503_in_production(self):
        ml = _load_module()
        req = ml.ScoreRequest(player_id="p1", tenant_id="t1", features={"deposit_sum_24h": 1})

        with patch.object(ml, "ENVIRONMENT", "production"), \
             patch.object(ml, "_db_engine", return_value=MagicMock()), \
             patch.object(ml, "_get_ml_challenger_pct", return_value=0), \
             patch.object(ml, "_load_tenant_model", return_value=None):
            with self.assertRaises(HTTPException) as exc:
                ml.score(req)

        self.assertEqual(exc.exception.status_code, 503)

    def test_train_blocks_synthetic_bootstrap_in_production(self):
        ml = _load_module()

        with patch.object(ml, "ENVIRONMENT", "production"), \
             patch.object(ml, "ML_ALLOW_SYNTHETIC_TRAINING", False), \
             patch.object(ml, "upload_model_artifact", return_value="memory://test/model.pkl"), \
             patch.object(ml, "register_model_db", return_value=None), \
             patch.object(ml, "_db_engine", return_value=MagicMock()):
            req = ml.TrainRequest(tenant_id="tenant-test", min_rows=50)
            with self.assertRaises(HTTPException) as exc:
                ml.train(req)

        self.assertEqual(exc.exception.status_code, 409)

    def test_reload_model_clears_all_variants_for_tenant(self):
        ml = _load_module()
        ml._model_cache.clear()
        ml._model_cache["tenant-a:champion"] = {"clf": object()}
        ml._model_cache["tenant-a:challenger"] = {"clf": object()}
        ml._model_cache["tenant-b:champion"] = {"clf": object()}

        resp = ml.reload_model(x_tenant_id="tenant-a")

        self.assertEqual(resp["status"], "cache_cleared")
        self.assertNotIn("tenant-a:champion", ml._model_cache)
        self.assertNotIn("tenant-a:challenger", ml._model_cache)
        self.assertIn("tenant-b:champion", ml._model_cache)


if __name__ == "__main__":
    unittest.main()
