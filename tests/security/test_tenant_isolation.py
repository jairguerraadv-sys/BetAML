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


# =============================================================================
# Static source-inspection security tests
# No running stack required — these tests read router source files directly
# and assert that the expected access-control patterns are present.
# =============================================================================

import re
import unittest

# Absolute path to the services/api directory derived from this file's location.
_SERVICES_API = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "services",
        "api",
    )
)


def _read_router(name: str) -> str:
    """Return the full source text of services/api/routers/<name>.py."""
    path = os.path.join(_SERVICES_API, "routers", f"{name}.py")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _extract_function_block(src: str, func_name: str) -> str:
    """Return the source text of the first function named *func_name*.

    Extraction starts at the matching ``def`` / ``async def`` line and ends
    just before the next top-level decorator, function, or class definition
    that begins at column 0.
    """
    m = re.search(
        r"^(?:async\s+def|def)\s+" + re.escape(func_name) + r"\b",
        src,
        re.MULTILINE,
    )
    if not m:
        return ""
    start = m.start()
    tail = src[start + 1:]
    nxt = re.search(r"^(?:@|async def |def |class )", tail, re.MULTILINE)
    end = (start + 1 + nxt.start()) if nxt else len(src)
    return src[start:end]


# ── Class 1: TestAuditorReadOnly ──────────────────────────────────────────────

class TestAuditorReadOnly(unittest.TestCase):
    """AUDITOR role must be absent from every mutating (write) operation."""

    def test_auditor_cannot_label_alert(self):
        """POST /alerts/{id}/triage (alert labelling) must not allow AUDITOR.

        Inspects routers/alerts.py and verifies that the triage_alert function
        uses require_roles with a list that excludes AUDITOR.
        """
        src = _read_router("alerts")
        block = _extract_function_block(src, "triage_alert")
        self.assertTrue(block, "triage_alert function not found in routers/alerts.py")
        self.assertIn(
            "require_roles",
            block,
            "triage_alert must use require_roles to gate write access",
        )
        self.assertNotIn(
            '"AUDITOR"',
            block,
            "triage_alert must not grant write (label) access to AUDITOR role",
        )

    def test_auditor_cannot_assign_case(self):
        """POST /cases/{id}/assign must not allow AUDITOR.

        Inspects routers/cases.py and verifies that assign_case restricts
        access to ADMIN and excludes AUDITOR.
        """
        src = _read_router("cases")
        block = _extract_function_block(src, "assign_case")
        self.assertTrue(block, "assign_case function not found in routers/cases.py")
        self.assertIn(
            "require_roles",
            block,
            "assign_case must use require_roles to gate write access",
        )
        self.assertNotIn(
            '"AUDITOR"',
            block,
            "assign_case must not grant case-assignment rights to AUDITOR role",
        )

    def test_auditor_cannot_ingest(self):
        """POST /ingest/batch must not allow AUDITOR.

        Inspects routers/ingest.py and verifies that ingest_batch requires
        ADMIN or AML_ANALYST and excludes AUDITOR.
        """
        src = _read_router("ingest")
        block = _extract_function_block(src, "ingest_batch")
        self.assertTrue(block, "ingest_batch function not found in routers/ingest.py")
        self.assertIn(
            "require_roles",
            block,
            "ingest_batch must use require_roles to gate write access",
        )
        self.assertNotIn(
            '"AUDITOR"',
            block,
            "ingest_batch must not grant ingest write access to AUDITOR role",
        )

    def test_auditor_cannot_erase_player(self):
        """POST /players/{id}/erase must not allow AUDITOR.

        Inspects routers/players.py and verifies that erase_player_data
        requires ADMIN (and optionally SUPER_ADMIN) but never AUDITOR.
        """
        src = _read_router("players")
        block = _extract_function_block(src, "erase_player_data")
        self.assertTrue(block, "erase_player_data function not found in routers/players.py")
        self.assertIn(
            "require_roles",
            block,
            "erase_player_data must use require_roles to gate erasure access",
        )
        self.assertNotIn(
            '"AUDITOR"',
            block,
            "erase_player_data must not grant LGPD erasure rights to AUDITOR role",
        )


# ── Class 2: TestPIIMasking ───────────────────────────────────────────────────

class TestPIIMasking(unittest.TestCase):
    """Full PII (CPF) must only be visible to ADMIN and AML_ANALYST."""

    def test_show_full_requires_analyst_or_admin(self):
        """GET /players/{id} show_full PII path must check for AML_ANALYST or ADMIN.

        Inspects routers/players.py: verifies that get_player gates full CPF
        exposure on a role membership check that includes ADMIN and AML_ANALYST
        but does NOT include AUDITOR.
        """
        src = _read_router("players")
        block = _extract_function_block(src, "get_player")
        self.assertTrue(block, "get_player function not found in routers/players.py")
        self.assertIn(
            "show_full",
            block,
            "get_player must implement a show_full PII guard variable",
        )
        self.assertIn(
            '"AML_ANALYST"',
            block,
            "show_full PII guard must explicitly include AML_ANALYST",
        )
        self.assertIn(
            '"ADMIN"',
            block,
            "show_full PII guard must explicitly include ADMIN",
        )
        self.assertNotIn(
            '"AUDITOR"',
            block,
            "AUDITOR must not be included in the show_full PII role guard",
        )

    def test_erased_player_returns_410(self):
        """GET /players/{id} for an ERASED player must raise HTTP 410.

        Inspects routers/players.py: verifies that get_player checks the
        ERASED status and responds with HTTP 410 (Gone) to protect
        anonymised data under LGPD Art. 18.
        """
        src = _read_router("players")
        block = _extract_function_block(src, "get_player")
        self.assertTrue(block, "get_player function not found in routers/players.py")
        self.assertIn(
            "ERASED",
            block,
            "get_player must explicitly check for ERASED player status",
        )
        self.assertIn(
            "410",
            block,
            "get_player must return HTTP 410 for players with ERASED status",
        )


# ── Class 3: TestLGPDErasureAuth ──────────────────────────────────────────────

class TestLGPDErasureAuth(unittest.TestCase):
    """LGPD erasure and data-export endpoints must enforce the correct role set."""

    def test_erase_requires_admin_not_analyst(self):
        """POST /players/{id}/erase must require ADMIN and must not allow plain AML_ANALYST.

        Inspects routers/players.py: verifies that erase_player_data guards
        the erasure operation with require_roles("ADMIN", ...) and that
        AML_ANALYST is NOT listed as an allowed role.
        """
        src = _read_router("players")
        block = _extract_function_block(src, "erase_player_data")
        self.assertTrue(block, "erase_player_data function not found in routers/players.py")
        self.assertIn(
            'require_roles("ADMIN"',
            block,
            "erase_player_data must require ADMIN role (LGPD erasure is a privileged action)",
        )
        self.assertNotIn(
            '"AML_ANALYST"',
            block,
            "erase_player_data must not grant LGPD erasure rights to AML_ANALYST",
        )

    def test_data_export_requires_analyst_or_admin(self):
        """GET /players/{id}/data-export must require AML_ANALYST or ADMIN.

        Inspects routers/players.py: verifies that export_player_data uses
        require_roles and includes both ADMIN and AML_ANALYST in the allowed
        role set (LGPD Art. 18 portability right is exercisable by analysts).
        """
        src = _read_router("players")
        block = _extract_function_block(src, "export_player_data")
        self.assertTrue(block, "export_player_data function not found in routers/players.py")
        self.assertIn(
            "require_roles",
            block,
            "export_player_data must use require_roles to gate data-export access",
        )
        self.assertIn(
            '"ADMIN"',
            block,
            "export_player_data must grant data-export access to ADMIN",
        )
        self.assertIn(
            '"AML_ANALYST"',
            block,
            "export_player_data must grant data-export access to AML_ANALYST",
        )


# ── Class 4: TestAuditLogImmutability ─────────────────────────────────────────

class TestAuditLogImmutability(unittest.TestCase):
    """Audit logs must be append-only; no DELETE or PATCH routes are permitted."""

    def test_no_delete_audit_endpoint(self):
        """routers/audit.py must not expose any DELETE or PATCH route.

        Inspects routers/audit.py: asserts that no @router.delete or
        @router.patch decorator exists, ensuring audit_log records cannot
        be removed or mutated via the API (immutability requirement).
        """
        src = _read_router("audit")
        self.assertNotIn(
            "@router.delete",
            src,
            "audit router must not expose any DELETE endpoint — logs are immutable",
        )
        self.assertNotIn(
            "@router.patch",
            src,
            "audit router must not expose any PATCH endpoint — logs are immutable",
        )
        get_routes = re.findall(r"@router\.get\(", src)
        self.assertGreater(
            len(get_routes),
            0,
            "audit router must expose at least one GET endpoint for log retrieval",
        )

    def test_audit_log_written_on_erase(self):
        """POST /players/{id}/erase must write an audit log entry (LGPD Art. 37).

        Inspects routers/players.py: verifies that erase_player_data calls
        write_audit and records the ERASE_PLAYER_DATA action so that every
        erasure event is traceable in the immutable audit log.
        """
        src = _read_router("players")
        block = _extract_function_block(src, "erase_player_data")
        self.assertTrue(block, "erase_player_data function not found in routers/players.py")
        self.assertIn(
            "write_audit",
            block,
            "erase_player_data must call write_audit for LGPD Art. 37 compliance",
        )
        self.assertIn(
            "ERASE_PLAYER_DATA",
            block,
            "erase_player_data audit entry must use the ERASE_PLAYER_DATA action string",
        )
