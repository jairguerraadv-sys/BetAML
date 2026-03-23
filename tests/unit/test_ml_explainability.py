from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def test_build_ml_explainability_prefers_top_drivers_and_baseline_delta():
    from routers.alerts import _build_ml_explainability

    alert = SimpleNamespace(
        id="alert-1",
        anomaly_score=0.91,
        evidence={
            "model_id": "model-123",
            "top_drivers": ["deposit_sum_24h", "night_activity_ratio"],
            "feature_snapshot": {
                "deposit_sum_24h": 12000,
                "baseline_avg_daily_deposit": 1500,
                "night_activity_ratio": 0.82,
            },
        },
    )

    result = _build_ml_explainability(alert)

    assert result is not None
    assert result["model_id"] == "model-123"
    assert result["top_features"][0]["feature"] == "deposit_sum_24h"
    assert result["top_features"][0]["baseline_value"] == 1500.0
    assert result["top_features"][0]["delta"] == 10500.0


def test_build_ml_explainability_returns_none_without_ml_context():
    from routers.alerts import _build_ml_explainability

    alert = SimpleNamespace(
        id="alert-2",
        anomaly_score=None,
        evidence={},
    )

    assert _build_ml_explainability(alert) is None
