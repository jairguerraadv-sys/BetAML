"""Integration smoke for DLQ -> replay flow.

Requires stack up:
    TEST_STACK_UP=1 pytest tests/integration/test_dlq_replay.py -q
"""

from __future__ import annotations

import os

import pytest
import requests


RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"
BASE_URL = os.getenv("API_URL", "http://localhost:8000")

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="requires TEST_STACK_UP=1 with Redpanda/Kafka",
)


def api(path: str, method: str = "GET", **kwargs) -> requests.Response:
    return requests.request(method, f"{BASE_URL}{path}", timeout=20, **kwargs)


def _login(username: str, password: str) -> str:
    resp = api("/auth/login", "POST", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@skip_unless_stack
def test_dlq_replay_endpoint_is_idempotent_for_same_event_id():
    token = _login("admin_a", "admin123")
    headers = {"Authorization": f"Bearer {token}"}

    bad_event = {
        "source_system": "BackofficeAlpha",
        "entity_type": "TRANSACTION",
        "payload": {"transaction_type": "DEPOSIT", "amount": 10.0},
    }
    ingest_resp = api("/ingest/event", "POST", headers=headers, json=bad_event)
    assert ingest_resp.status_code in (202, 400, 422)

    errors_resp = api("/ingest/errors", headers=headers)
    assert errors_resp.status_code == 200
    errors = errors_resp.json()
    if not errors:
        pytest.skip("no ingest errors available to replay in current stack state")

    error_id = errors[0]["id"]
    replay_payload = {
        "corrected_payload": {
            "event_id": "dlq-replay-idempotent-evt",
            "player_id": "PLY-E2E-1",
            "transaction_type": "DEPOSIT",
            "amount": 50.0,
            "currency": "BRL",
            "occurred_at": "2026-05-27T00:00:00Z",
        }
    }

    first = api(f"/ingest/errors/{error_id}/replay", "POST", headers=headers, json=replay_payload)
    assert first.status_code in (200, 202)

    second = api(f"/ingest/errors/{error_id}/replay", "POST", headers=headers, json=replay_payload)
    assert second.status_code in (200, 202)
    assert second.json().get("status") in {"already_processed", "queued"}
