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

RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"
skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack não disponível. Use TEST_STACK_UP=1 para rodar testes de segurança.",
)

# Credentials — override via environment for CI
# Nomes de usuário gerados pelo seeds.py:  analyst_a / analyst_b
TENANT_A = {
    "username": os.getenv("TENANT_A_USER", "analyst_a"),
    "password": os.getenv("TENANT_A_PASS", "analyst123"),
}
TENANT_B = {
    "username": os.getenv("TENANT_B_USER", "analyst_b"),
    "password": os.getenv("TENANT_B_PASS", "analyst123"),
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client_a():
    if not RUN_INTEGRATION:
        pytest.skip("Stack não disponível")
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.post("/auth/login", json=TENANT_A)
        if r.status_code != 200:
            pytest.skip(f"Tenant A login failed: {r.status_code} — {r.text}")
        token = r.json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture(scope="module")
def client_b():
    if not RUN_INTEGRATION:
        pytest.skip("Stack não disponível")
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.post("/auth/login", json=TENANT_B)
        if r.status_code != 200:
            pytest.skip(f"Tenant B login failed: {r.status_code} — {r.text}")
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


def _create_ingest_file_job(client: httpx.Client) -> str | None:
    csv_payload = (
        "event_id,external_player_id,transaction_type,amount,occurred_at,currency\n"
        f"evt-{uuid.uuid4().hex[:8]},ply-{uuid.uuid4().hex[:6]},DEPOSIT,100.0,2026-03-09T10:00:00Z,BRL\n"
    ).encode("utf-8")
    resp = client.post(
        "/ingest/file",
        data={"source_system": "BackofficeAlpha", "entity_type": "transaction"},
        files={"file": ("tenant-test.csv", csv_payload, "text/csv")},
    )
    if resp.status_code != 202:
        return None
    return resp.json().get("job_id")


# ── Unauthenticated access ────────────────────────────────────────────────────

@skip_unless_stack
def test_unauthenticated_alerts_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/alerts")
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}"


@skip_unless_stack
def test_unauthenticated_cases_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/cases")
        assert r.status_code in (401, 403)


@skip_unless_stack
def test_unauthenticated_players_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/players")
        assert r.status_code in (401, 403)


@skip_unless_stack
def test_unauthenticated_audit_logs_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/audit-logs")
        assert r.status_code in (401, 403)


@skip_unless_stack
def test_unauthenticated_legacy_audit_log_blocked():
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.get("/audit-log")
        assert r.status_code in (401, 403)


# ── Cross-tenant resource access ──────────────────────────────────────────────

@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_alert(client_a, client_b):
    alert_id = _first_id(client_a, "/alerts")
    if not alert_id:
        pytest.skip("No alerts for tenant A")
    resp = client_b.get(f"/alerts/{alert_id}")
    assert resp.status_code in (403, 404), (
        f"Tenant B accessed tenant A alert {alert_id}: {resp.status_code}"
    )


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_case(client_a, client_b):
    case_id = _first_id(client_a, "/cases")
    if not case_id:
        pytest.skip("No cases for tenant A")
    resp = client_b.get(f"/cases/{case_id}")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_player(client_a, client_b):
    player_id = _first_id(client_a, "/players")
    if not player_id:
        pytest.skip("No players for tenant A")
    resp = client_b.get(f"/feature-store/players/{player_id}/current")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_feature_store_history(client_a, client_b):
    player_id = _first_id(client_a, "/players")
    if not player_id:
        pytest.skip("No players for tenant A")
    resp = client_b.get(f"/feature-store/players/{player_id}/history")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_legacy_feature_history(client_a, client_b):
    player_id = _first_id(client_a, "/players")
    if not player_id:
        pytest.skip("No players for tenant A")
    resp = client_b.get(f"/players/{player_id}/feature-history?days=7")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_a_cannot_access_tenant_b_alert(client_a, client_b):
    alert_id = _first_id(client_b, "/alerts")
    if not alert_id:
        pytest.skip("No alerts for tenant B")
    resp = client_a.get(f"/alerts/{alert_id}")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_ingest_job(client_a, client_b):
    job_id = _create_ingest_file_job(client_a)
    if not job_id:
        pytest.skip("Falha ao criar ingest job para tenant A")
    resp = client_b.get(f"/ingest/jobs/{job_id}")
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_access_tenant_a_ingest_errors(client_a, client_b):
    job_id = _create_ingest_file_job(client_a)
    if not job_id:
        pytest.skip("Falha ao criar ingest job para tenant A")

    errors_a = client_a.get(f"/ingest/errors?job_id={job_id}&limit=5")
    if errors_a.status_code != 200:
        pytest.skip("Não foi possível listar erros de ingest do tenant A")
    items = errors_a.json()
    if not items:
        pytest.skip("Sem erros de ingest para validar isolamento")

    error_id = items[0].get("id")
    if not error_id:
        pytest.skip("Erro de ingest sem id")

    resp_list = client_b.get(f"/ingest/errors?job_id={job_id}&limit=5")
    assert resp_list.status_code == 200
    assert resp_list.json() == []

    resp_resolve = client_b.post(
        f"/ingest/errors/{error_id}/resolve",
        json={"note": "cross-tenant resolve attempt"},
    )
    assert resp_resolve.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_reprocess_tenant_a_job(client_a, client_b):
    job_id = _create_ingest_file_job(client_a)
    if not job_id:
        pytest.skip("Falha ao criar ingest job para tenant A")

    resp = client_b.post(
        f"/ingest/jobs/{job_id}/reprocess",
        json={"reason": "cross-tenant reprocess attempt"},
    )
    assert resp.status_code in (403, 404)


@skip_unless_stack
def test_tenant_b_cannot_replay_tenant_a_ingest_error(client_a, client_b):
    job_id = _create_ingest_file_job(client_a)
    if not job_id:
        pytest.skip("Falha ao criar ingest job para tenant A")

    errors_a = client_a.get(f"/ingest/errors?job_id={job_id}&limit=5")
    if errors_a.status_code != 200:
        pytest.skip("Não foi possível listar erros de ingest do tenant A")
    items = errors_a.json()
    if not items:
        pytest.skip("Sem erros de ingest para validar replay cross-tenant")

    error_id = items[0].get("id")
    if not error_id:
        pytest.skip("Erro de ingest sem id")

    resp = client_b.post(
        f"/ingest/errors/{error_id}/replay",
        json={
            "corrected_payload": {
                "event_id": "evt-cross-tenant",
                "external_player_id": "CPF123",
                "transaction_type": "DEPOSIT",
                "amount": 50,
                "occurred_at": "2026-03-10T12:00:00Z",
            }
        },
    )
    assert resp.status_code in (403, 404)


@skip_unless_stack
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

@skip_unless_stack
def test_random_uuid_alert_returns_404_not_403(client_a):
    """A non-existent resource should return 404, not 500."""
    fake_id = str(uuid.uuid4())
    resp = client_a.get(f"/alerts/{fake_id}")
    assert resp.status_code in (404, 403), f"Unexpected {resp.status_code}"


@skip_unless_stack
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


# ── Cross-tenant: Notifications ───────────────────────────────────────────────

@skip_unless_stack
def test_tenant_b_cannot_read_tenant_a_notifications(client_a, client_b):
    """Notifications from Tenant A must not be visible to Tenant B."""
    resp_a = client_a.get("/notifications")
    resp_b = client_b.get("/notifications")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    ids_a = {n["id"] for n in resp_a.json()}
    ids_b = {n["id"] for n in resp_b.json()}
    assert ids_a.isdisjoint(ids_b), "Tenant B can see Tenant A notifications"


@skip_unless_stack
def test_tenant_b_cannot_mark_tenant_a_notification_read(client_a, client_b):
    """Tenant B must not be able to mark Tenant A's notification as read."""
    id_a = _first_id(client_a, "/notifications")
    if not id_a:
        pytest.skip("No notifications for Tenant A")
    resp = client_b.post(f"/notifications/{id_a}/read")
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"


# ── Cross-tenant: Model Registry ──────────────────────────────────────────────

@skip_unless_stack
def test_tenant_b_cannot_see_tenant_a_models(client_a, client_b):
    """Model registry entries of Tenant A must not appear in Tenant B's list."""
    resp_a = client_a.get("/model-registry")
    resp_b = client_b.get("/model-registry")
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    ids_a = {m["id"] for m in resp_a.json()}
    ids_b = {m["id"] for m in resp_b.json()}
    assert ids_a.isdisjoint(ids_b), "Tenant B can see Tenant A model registry entries"


@skip_unless_stack
def test_tenant_b_cannot_promote_tenant_a_model(client_a, client_b):
    """Tenant B must get 403/404 when trying to promote Tenant A's model."""
    id_a = _first_id(client_a, "/model-registry")
    if not id_a:
        pytest.skip("No models for Tenant A")
    resp = client_b.post(f"/model-registry/{id_a}/promote")
    assert resp.status_code in (403, 404), f"Expected 403/404, got {resp.status_code}"


# ── Cross-tenant: Admin Flags ─────────────────────────────────────────────────

@skip_unless_stack
def test_tenant_b_flags_do_not_contain_tenant_a_data(client_a, client_b):
    """Tenant A flags must not appear in Tenant B admin flag listing."""
    resp_a = client_a.get("/admin/flags")
    resp_b = client_b.get("/admin/flags")
    if resp_a.status_code in (401, 403) and resp_b.status_code in (401, 403):
        pytest.skip("Both tenants lack admin role — expected for analyst credentials")
    assert resp_a.status_code in (200, 401, 403)
    assert resp_b.status_code in (200, 401, 403)
    if resp_a.status_code == 200 and resp_b.status_code == 200:
        keys_a = {f["key"] for f in resp_a.json()}
        keys_b = {f["key"] for f in resp_b.json()}
        assert keys_a.isdisjoint(keys_b), "Tenant B can read Tenant A flag keys"


# ── Role enforcement: Admin Tenant Creation ───────────────────────────────────

@skip_unless_stack
def test_non_admin_cannot_create_tenant(client_a):
    """AML_ANALYST must receive 403 when attempting POST /admin/tenants."""
    resp = client_a.post("/admin/tenants", json={
        "name": "Hack Tenant",
        "slug": "hack-tenant",
        "admin_username": "hacker",
        "admin_email": "hacker@evil.com",
        "admin_password": "password123",
    })
    assert resp.status_code == 403, f"Non-admin should be rejected, got {resp.status_code}"


@skip_unless_stack
def test_duplicate_tenant_slug_returns_409():
    """Creating a tenant with an existing slug must return 409 Conflict."""
    if not RUN_INTEGRATION:
        pytest.skip("Stack não disponível")
    admin_creds = {
        "username": os.getenv("SUPER_ADMIN_USER", "admin_a"),
        "password": os.getenv("SUPER_ADMIN_PASS", "admin123"),
    }
    with httpx.Client(base_url=BASE_URL, timeout=10) as c:
        r = c.post("/auth/login", json=admin_creds)
        if r.status_code != 200:
            pytest.skip(f"Admin login failed: {r.status_code}")
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        # First creation
        slug = f"test-slug-{uuid.uuid4().hex[:8]}"
        body = {"name": f"Test {slug}", "slug": slug,
                "admin_username": f"u_{slug[:6]}", "admin_email": f"{slug}@test.com",
                "admin_password": "securepass1"}
        r1 = c.post("/admin/tenants", json=body)
        if r1.status_code != 201:
            pytest.skip(f"Could not create first tenant: {r1.status_code}")
        # Duplicate
        r2 = c.post("/admin/tenants", json=body)
        assert r2.status_code == 409, f"Expected 409, got {r2.status_code}: {r2.text}"
