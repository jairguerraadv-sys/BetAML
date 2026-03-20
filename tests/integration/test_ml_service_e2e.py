"""
E2E tests for ML Service endpoints (Task 12).

Requires Docker stack running (infra/docker-compose.yml).
By default these tests are skipped. To run:

  TEST_STACK_UP=1 pytest tests/integration/test_ml_service_e2e.py -v

Assumptions:
- ml-service is reachable at localhost:8001
- Postgres/MinIO are up (ml-service uses them for /train and model load)
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"
ML_URL = os.getenv("ML_URL", "http://localhost:8001")

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack não disponível. Use TEST_STACK_UP=1 para rodar testes de integração.",
)


def ml(path: str, method: str = "GET", **kwargs) -> requests.Response:
    return requests.request(method, f"{ML_URL}{path}", timeout=30, **kwargs)


@skip_unless_stack
def test_ml_health():
    resp = ml("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"


@skip_unless_stack
def test_train_then_score_then_shap_smoke():
    tenant_id = str(uuid.uuid4())

    train_resp = ml("/train", "POST", json={"tenant_id": tenant_id, "min_rows": 50})
    assert train_resp.status_code == 200, train_resp.text
    train_body = train_resp.json()
    assert "model_id" in train_body

    player_id = str(uuid.uuid4())
    score_resp = ml(
        "/score",
        "POST",
        headers={"X-Request-Id": "e2e-ml-service"},
        json={"player_id": player_id, "tenant_id": tenant_id, "features": {"deposit_sum_24h": 1000}},
    )
    assert score_resp.status_code == 200, score_resp.text
    score_body = score_resp.json()
    assert score_body["tenant_id"] == tenant_id
    assert score_body["player_id"] == player_id
    assert 0.0 <= float(score_body["anomaly_score"]) <= 1.0

    shap_resp = ml(
        "/score/shap",
        "POST",
        json={"tenant_id": tenant_id, "player_id": player_id, "features": {"deposit_sum_24h": 1000}},
    )
    assert shap_resp.status_code in (200, 404), shap_resp.text
    if shap_resp.status_code == 200:
        shap_body = shap_resp.json()
        assert shap_body["tenant_id"] == tenant_id
        assert shap_body["player_id"] == player_id
        assert isinstance(shap_body.get("shap_values"), dict)
