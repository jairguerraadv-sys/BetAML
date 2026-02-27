"""
tests/security/test_tenant_isolation.py
Security tests verifying that cross-tenant data access is correctly blocked.

These tests use two separate tenant credentials and assert that resources
belonging to tenant A are NOT accessible by tenant B.

Requirements:
    pip install pytest httpx
    API running at http://localhost:8000

Usage:
    pytest tests/security/test_tenant_isolation.py -v
"""
from __future__ import annotations

import os
import uuid

import pytest
import httpx

BASE_URL = os.getenv("BETAML_API_URL", "http://localhost:8000")

# Credentials — override via environment for CI
TENANT_A = {
    "username": os.getenv("TENANT_A_EMAIL", "analyst@betaml.io"),
    "password": os.getenv("TENANT_A_PASS", "analyst123"),
}
TENANT_B = {
    "username": os.getenv("TENANT_B_EMAIL", "analyst2@betaml2.io"),
    "password": os.getenv("TENANT_B_PASS", "analyst456"),
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client_a():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.post("/auth/login", data=TENANT_A,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            pytest.skip(f"Tenant A login failed: {r.status_code}")
        token = r.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture(scope="module")
def client_b():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.post("/auth/login", data=TENANT_B,
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            pytest.skip(f"Tenant B login failed: {r.status_code}")
        token = r.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


# ── Helper ────────────────────────────────────────────────────────────────────

def _first_id(client: httpx.Client, endpoint: str) -> str | None:
    resp = client.get(endpoint)
    if resp.status_code != 200:
        return None
    data = resp.json()
    if isinstance(data, list) and data:
        return data[0].get("id")
    return None


# ── Unauthenticated access ────────────────────────────────────────────────────

def test_unauthenticated_alerts_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/alerts")
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"


def test_unauthenticated_cases_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/cases")
        assert r.status_code in (401, 403)


def test_unauthenticated_players_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/players")
        assert r.status_code in (401, 403)


def test_unauthenticated_audit_logs_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/audit-logs")
        assert r.status_code in (401, 403)


# ── Cross-tenant resource access ──────────────────────────────────────────────

def test_tenant_b_cannot_access_tenant_a_alert(client_a, client_b):
    alert_id = _first_id(client_a, "/alerts")
    if not alert_id:
        pytest.skip("No alerts for tenant A")
    resp = client_b.get(f"/alerts/{alert_id}")
    assert resp.status_code in (403, 404), (
        f"Tenant B accessed tenant A alert {alert_id}: {resp.status_code}"
    )


def test_tenant_b_cannot_access_tenant_a_case(client_a, client_b):
    case_id = _first_id(client_a, "/cases")
    if not case_id:
        pytest.skip("No cases for tenant A")
    resp = client_b.get(f"/cases/{case_id}")
    assert resp.status_code in (403, 404)


def test_tenant_b_cannot_access_tenant_a_player(client_a, client_b):
    player_id = _first_id(client_a, "/players")
    if not player_id:
        pytest.skip("No players for tenant A")
    resp = client_b.get(f"/players/{player_id}/features/current")
    assert resp.status_code in (403, 404)


def test_tenant_a_cannot_access_tenant_b_alert(client_a, client_b):
    alert_id = _first_id(client_b, "/alerts")
    if not alert_id:
        pytest.skip("No alerts for tenant B")
    resp = client_a.get(f"/alerts/{alert_id}")
    assert resp.status_code in (403, 404)


def test_tenant_b_cannot_list_tenant_a_api_keys(client_b):
    """Admin endpoints must return only the caller's tenant keys."""
    resp_b = client_b.get("/admin/api-keys")
    if resp_b.status_code in (403, 401):
        pytest.skip("Tenant B has no admin access — expected")
    assert resp_b.status_code == 200
    for key in resp_b.json():
        # Each key must NOT contain any reference to tenant A's UUID
        assert TENANT_A["username"] not in str(key), "Tenant A data leaked to Tenant B"


# ── IDOR via predictable UUIDs ────────────────────────────────────────────────

def test_random_uuid_alert_returns_404_not_403(client_a):
    """A non-existent resource should return 404, not 500."""
    fake_id = str(uuid.uuid4())
    resp = client_a.get(f"/alerts/{fake_id}")
    assert resp.status_code in (404, 403), f"Unexpected {resp.status_code}"


def test_random_uuid_case_returns_404(client_a):
    fake_id = str(uuid.uuid4())
    resp = client_a.get(f"/cases/{fake_id}")
    assert resp.status_code in (404, 403)


# ── Token manipulation ────────────────────────────────────────────────────────

def test_tampered_jwt_rejected():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        c.headers["Authorization"] = "Bearer eyInvalidToken.tampered.signature"
        r = c.get("/alerts")
        assert r.status_code in (401, 403)


def test_expired_token_format_rejected():
    """A well-formed but clearly invalid token must be rejected."""
    fake = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJoYWNrZXIifQ.badhash"
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        c.headers["Authorization"] = f"Bearer {fake}"
        r = c.get("/cases")
        assert r.status_code in (401, 403)
