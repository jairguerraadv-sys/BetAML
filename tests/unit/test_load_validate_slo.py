from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../tests/load"))

from validate_slo import evaluate_thresholds


def test_validate_slo_passes_when_thresholds_are_met():
    ok, evidence = evaluate_thresholds(
        {
            "request_name": "POST /ingest/batch",
            "request_count": "1000",
            "failure_count": "2",
            "p95_ms": "450",
            "rps": "120",
        },
        batch_size=10,
        min_rps=50,
        min_event_rps=1000,
        max_p95_ms=500,
        max_failure_rate_pct=1,
    )

    assert ok is True
    assert "load_slo=PASS" in evidence


def test_validate_slo_fails_when_thresholds_are_violated():
    ok, evidence = evaluate_thresholds(
        {
            "request_name": "POST /ingest/batch",
            "request_count": "1000",
            "failure_count": "25",
            "p95_ms": "900",
            "rps": "20",
        },
        batch_size=10,
        min_rps=50,
        min_event_rps=600,
        max_p95_ms=500,
        max_failure_rate_pct=1,
    )

    assert ok is False
    assert "load_slo=FAIL" in evidence
    assert any(line.startswith("rps_below_threshold") for line in evidence)
    assert any(line.startswith("failure_rate_above_threshold") for line in evidence)
    assert any(line.startswith("p95_above_threshold") for line in evidence)