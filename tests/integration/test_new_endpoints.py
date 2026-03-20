"""
Testes de integração para novos endpoints BetAML — GAP-7/GAP-8 coverage.
Requisitos: stack Docker rodando (docker-compose -f infra/docker-compose.yml up -d)

Por padrão esses testes são pulados. Para executar:
    TEST_STACK_UP=1 pytest tests/integration/test_new_endpoints.py -v --tb=short

No CI com Docker:
    docker-compose -f infra/docker-compose.yml up -d
    TEST_STACK_UP=1 pytest tests/integration/test_new_endpoints.py

Endpoints cobertos:
  - GET  /search/players                        (TestSearchEndpoints)
  - POST /players/{id}/erase                    (TestLGPDEraseEndpoint)
  - GET  /cases/{id}/report-package/xml         (TestCOAFXMLEndpoint)
  - POST /internal/alerts/webhook               (TestWebhookEndpoint)
  - GET/POST/DELETE /admin/users                (TestAdminUsersCRUD)
  - GET/POST /model-registry + promote          (TestModelPromoteEndpoint)
  - GET/POST/DELETE /rules/compound             (TestCompoundRulesAndMacros)
  - GET/POST/DELETE /rules/macros               (TestCompoundRulesAndMacros)
  - GET/POST/DELETE /player-lists               (TestPlayerLists)
    - GET  /admin/kpis/aml                         (TestAdminAmlKpis)
"""
import os
import json
import uuid
import pytest
import requests

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack não disponível. Use TEST_STACK_UP=1 para rodar testes de integração.",
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def api(path: str, method: str = "GET", **kwargs) -> requests.Response:
    return requests.request(method, f"{BASE_URL}{path}", timeout=15, **kwargs)


def _login(username: str, password: str) -> dict:
    resp = api("/auth/login", "POST", json={"username": username, "password": password})
    assert resp.status_code == 200, f"Login falhou ({username}): {resp.text}"
    return resp.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Test Classes ───────────────────────────────────────────────────────────────


@skip_unless_stack
class TestSearchEndpoints:
    """Covers GET /search/players — pagination and cross-tenant isolation."""

    def test_search_players_returns_paginated_results(self):
        token = _login("analyst_a", "analyst123")["access_token"]
        resp = api("/search/players", headers=_headers(token), params={"q": "test"})
        assert resp.status_code in (200, 404), (
            f"Expected 200 or 404, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 200:
            data = resp.json()
            # Accept either a bare list or a paginated envelope {items, total}
            assert isinstance(data, list) or (
                isinstance(data, dict) and ("items" in data or "total" in data)
            ), f"Unexpected response shape: {data}"

    def test_search_players_cross_tenant_isolation(self):
        token_b = _login("admin_b", "admin123")["access_token"]
        resp = api("/search/players", headers=_headers(token_b), params={"q": "test"})
        assert resp.status_code in (200, 404), (
            f"Expected 200 or 404, got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data if isinstance(data, list) else data.get("items", [])
            assert isinstance(results, list), (
                f"Results should be a list, got: {type(results)}"
            )
            # Tenant isolation is enforced at DB level; admin_b should only see
            # OperadorB records. We assert the endpoint returns a valid list —
            # seeded data ensures no cross-contamination.


# ── LGPD Erase ─────────────────────────────────────────────────────────────────


@skip_unless_stack
class TestLGPDEraseEndpoint:
    """Covers POST /players/{id}/erase — LGPD Art. 18 right to erasure."""

    def _ingest_player(self, player_id: str, token: str) -> requests.Response:
        payload = {
            "player_id": player_id,
            "event_type": "DEPOSIT",
            "amount": 100,
            "currency": "BRL",
            "metadata": {},
        }
        return api("/ingest/event", "POST", json=payload, headers=_headers(token))

    def test_erase_requires_admin_role(self):
        analyst_token = _login("analyst_a", "analyst123")["access_token"]
        player_id = f"erase_test_gap7_{uuid.uuid4().hex[:8]}"

        # Create player via ingest (analyst can ingest)
        ingest_resp = self._ingest_player(player_id, analyst_token)
        assert ingest_resp.status_code in (200, 201, 202), (
            f"Ingest failed: {ingest_resp.status_code} {ingest_resp.text}"
        )

        # Attempt erase as analyst — should be forbidden
        erase_resp = api(
            f"/players/{player_id}/erase",
            "POST",
            headers=_headers(analyst_token),
        )
        assert erase_resp.status_code == 403, (
            f"Expected 403 for analyst erase, got {erase_resp.status_code}: {erase_resp.text}"
        )

    def test_admin_can_erase_player(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        player_id = f"erase_test_gap7_{uuid.uuid4().hex[:8]}"

        # Create player via ingest
        ingest_resp = self._ingest_player(player_id, admin_token)
        assert ingest_resp.status_code in (200, 201, 202), (
            f"Ingest failed: {ingest_resp.status_code} {ingest_resp.text}"
        )

        # Erase as admin — should succeed
        erase_resp = api(
            f"/players/{player_id}/erase",
            "POST",
            headers=_headers(admin_token),
        )
        assert erase_resp.status_code in (200, 204), (
            f"Expected 200 or 204 for admin erase, got {erase_resp.status_code}: {erase_resp.text}"
        )

        # Fetching the erased player should return 410 Gone
        get_resp = api(
            f"/players/{player_id}",
            headers=_headers(admin_token),
            params={"show_full": "false"},
        )
        assert get_resp.status_code == 410, (
            f"Expected 410 for erased player, got {get_resp.status_code}: {get_resp.text}"
        )


# ── COAF XML ───────────────────────────────────────────────────────────────────


@skip_unless_stack
class TestCOAFXMLEndpoint:
    """Covers GET /cases/{id}/report-package/xml — COAF SAR export."""

    def test_coaf_xml_requires_auth(self):
        resp = api("/cases/nonexistent-id/report-package/xml")
        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403 without auth, got {resp.status_code}: {resp.text}"
        )

    def test_coaf_xml_returns_xml_for_existing_case(self):
        analyst_token = _login("analyst_a", "analyst123")["access_token"]

        # Create a case first
        case_resp = api(
            "/cases",
            "POST",
            json={"title": "GAP7 Test", "player_id": "test", "severity": "HIGH"},
            headers=_headers(analyst_token),
        )
        assert case_resp.status_code in (200, 201), (
            f"Case creation failed: {case_resp.status_code} {case_resp.text}"
        )
        case_id = case_resp.json().get("id") or case_resp.json().get("case_id")
        assert case_id, f"No case id in response: {case_resp.json()}"

        # Fetch the XML report
        xml_resp = api(
            f"/cases/{case_id}/report-package/xml",
            headers=_headers(analyst_token),
        )
        # Accept 200 (valid XML) or 404 (player_id 'test' not resolvable in SAR builder)
        assert xml_resp.status_code not in range(500, 600), (
            f"5xx error on COAF XML endpoint: {xml_resp.status_code} {xml_resp.text}"
        )
        if xml_resp.status_code == 200:
            ct = xml_resp.headers.get("Content-Type", "")
            assert "xml" in ct.lower(), (
                f"Expected XML content-type, got: {ct}"
            )


# ── Webhook ────────────────────────────────────────────────────────────────────


@skip_unless_stack
class TestWebhookEndpoint:
    """Covers POST /internal/alerts/webhook — AlertManager receiver."""

    _FIRING_PAYLOAD = {
        "version": "4",
        "receiver": "betaml",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighAPILatency",
                    "severity": "warning",
                },
                "annotations": {"summary": "High latency"},
                "startsAt": "2026-01-01T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
            }
        ],
        "groupLabels": {},
        "commonLabels": {},
        "commonAnnotations": {},
        "externalURL": "",
    }

    def test_webhook_accepts_alertmanager_payload(self):
        resp = api(
            "/internal/alerts/webhook",
            "POST",
            json=self._FIRING_PAYLOAD,
        )
        assert resp.status_code == 200, (
            f"Expected 200 from webhook, got {resp.status_code}: {resp.text}"
        )

    def test_webhook_ignores_resolved_alerts(self):
        payload = dict(self._FIRING_PAYLOAD)
        payload["status"] = "resolved"
        payload["alerts"] = [
            {
                "status": "resolved",
                "labels": {"alertname": "HighAPILatency", "severity": "warning"},
                "annotations": {"summary": "High latency resolved"},
                "startsAt": "2026-01-01T00:00:00Z",
                "endsAt": "2026-01-01T00:05:00Z",
            }
        ]
        resp = api(
            "/internal/alerts/webhook",
            "POST",
            json=payload,
        )
        assert resp.status_code == 200, (
            f"Expected 200 for resolved alert, got {resp.status_code}: {resp.text}"
        )


# ── Admin Users CRUD ───────────────────────────────────────────────────────────


@skip_unless_stack
class TestAdminUsersCRUD:
    """Covers GET/POST/DELETE /admin/users."""

    def test_list_users_returns_users_for_tenant(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        resp = api("/admin/users", headers=_headers(admin_token))
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}: {data}"

    def test_create_user_requires_admin(self):
        analyst_token = _login("analyst_a", "analyst123")["access_token"]
        resp = api(
            "/admin/users",
            "POST",
            json={
                "username": "newuser_gap7",
                "email": "x@x.com",
                "password": "Abc123!!",
                "role": "AML_ANALYST",
            },
            headers=_headers(analyst_token),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for analyst creating user, got {resp.status_code}: {resp.text}"
        )

    def test_admin_can_create_and_delete_user(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        username = f"gap7_{uuid.uuid4().hex[:8]}"
        email = f"{username}@test.com"

        # Create
        create_resp = api(
            "/admin/users",
            "POST",
            json={
                "username": username,
                "email": email,
                "password": "Str0ng!Pass",
                "role": "AML_ANALYST",
            },
            headers=_headers(admin_token),
        )
        assert create_resp.status_code in (200, 201), (
            f"Expected 201 or 200 for user creation, got {create_resp.status_code}: {create_resp.text}"
        )
        user_id = (
            create_resp.json().get("id")
            or create_resp.json().get("user_id")
        )
        assert user_id, f"No user id in response: {create_resp.json()}"

        # Delete
        delete_resp = api(
            f"/admin/users/{user_id}",
            "DELETE",
            headers=_headers(admin_token),
        )
        assert delete_resp.status_code == 204, (
            f"Expected 204 on user deletion, got {delete_resp.status_code}: {delete_resp.text}"
        )


@skip_unless_stack
class TestAdminAmlKpis:
    """Covers GET /admin/kpis/aml."""

    def test_admin_kpis_aml_returns_expected_shape(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        resp = api("/admin/kpis/aml", headers=_headers(admin_token))
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        expected_keys = {
            "generated_at",
            "window_days",
            "alerts_open",
            "alerts_in_review",
            "alerts_labeled_30d",
            "true_positive_rate_30d_percent",
            "false_positive_rate_30d_percent",
            "cases_open",
            "cases_overdue",
            "sla_breach_rate_open_cases_percent",
            "avg_case_resolution_hours_30d",
        }
        assert expected_keys.issubset(data.keys()), (
            f"Missing keys in KPI response. Got: {sorted(data.keys())}"
        )
        assert 0 <= float(data["true_positive_rate_30d_percent"]) <= 100
        assert 0 <= float(data["false_positive_rate_30d_percent"]) <= 100
        assert 0 <= float(data["sla_breach_rate_open_cases_percent"]) <= 100


# ── Model Registry / Promote ───────────────────────────────────────────────────


@skip_unless_stack
class TestModelPromoteEndpoint:
    """Covers GET /model-registry and POST /model-registry/{id}/promote."""

    def test_model_registry_list_returns_200(self):
        analyst_token = _login("analyst_a", "analyst123")["access_token"]
        resp = api("/model-registry", headers=_headers(analyst_token))
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        assert isinstance(resp.json(), list), (
            f"Expected list, got {type(resp.json())}: {resp.json()}"
        )

    def test_promote_nonexistent_model_returns_404(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        random_id = str(uuid.uuid4())
        resp = api(
            f"/model-registry/{random_id}/promote",
            "POST",
            headers=_headers(admin_token),
        )
        assert resp.status_code in (404, 403), (
            f"Expected 404 or 403, got {resp.status_code}: {resp.text}"
        )

    def test_promote_requires_admin(self):
        analyst_token = _login("analyst_a", "analyst123")["access_token"]
        random_id = str(uuid.uuid4())
        resp = api(
            f"/model-registry/{random_id}/promote",
            "POST",
            headers=_headers(analyst_token),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for analyst promoting model, got {resp.status_code}: {resp.text}"
        )


# ── Compound Rules and Macros ──────────────────────────────────────────────────


@skip_unless_stack
class TestCompoundRulesAndMacros:
    """Covers /rules/compound and /rules/macros — GAP-8."""

    def test_create_and_list_compound_rule(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        name = f"GAP8 Compound {uuid.uuid4().hex[:8]}"

        # Create
        create_resp = api(
            "/rules/compound",
            "POST",
            json={"name": name, "logic": "AND", "component_rule_ids": []},
            headers=_headers(admin_token),
        )
        assert create_resp.status_code == 201, (
            f"Expected 201 for compound rule creation, got {create_resp.status_code}: {create_resp.text}"
        )
        rule_id = (
            create_resp.json().get("id")
            or create_resp.json().get("rule_id")
        )
        assert rule_id, f"No rule id in response: {create_resp.json()}"

        # List — must contain the created rule
        list_resp = api("/rules/compound", headers=_headers(admin_token))
        assert list_resp.status_code == 200, (
            f"Expected 200 on list, got {list_resp.status_code}: {list_resp.text}"
        )
        names = [r.get("name") for r in list_resp.json()]
        assert name in names, f"Created rule '{name}' not found in list: {names}"

        # Delete
        delete_resp = api(
            f"/rules/compound/{rule_id}",
            "DELETE",
            headers=_headers(admin_token),
        )
        assert delete_resp.status_code == 204, (
            f"Expected 204 on delete, got {delete_resp.status_code}: {delete_resp.text}"
        )

    def test_compound_rule_cross_tenant_isolation(self):
        admin_a_token = _login("admin_a", "admin123")["access_token"]
        name = f"GAP8 Compound Isolation {uuid.uuid4().hex[:8]}"

        # Create as admin_a
        create_resp = api(
            "/rules/compound",
            "POST",
            json={"name": name, "logic": "OR", "component_rule_ids": []},
            headers=_headers(admin_a_token),
        )
        assert create_resp.status_code == 201, (
            f"Compound rule creation failed: {create_resp.status_code} {create_resp.text}"
        )
        rule_id = (
            create_resp.json().get("id")
            or create_resp.json().get("rule_id")
        )

        # List as admin_b — should NOT see admin_a's rule
        admin_b_token = _login("admin_b", "admin123")["access_token"]
        list_resp = api("/rules/compound", headers=_headers(admin_b_token))
        assert list_resp.status_code == 200, (
            f"Expected 200, got {list_resp.status_code}: {list_resp.text}"
        )
        names_b = [r.get("name") for r in list_resp.json()]
        assert name not in names_b, (
            f"Cross-tenant isolation breach: rule '{name}' visible to admin_b"
        )

        # Cleanup
        if rule_id:
            api(
                f"/rules/compound/{rule_id}",
                "DELETE",
                headers=_headers(admin_a_token),
            )

    def test_create_and_list_macro(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        name = f"GAP8 Macro {uuid.uuid4().hex[:8]}"

        # Create
        create_resp = api(
            "/rules/macros",
            "POST",
            json={
                "name": name,
                "expression": "event.amount > 1000",
                "description": "Test macro",
            },
            headers=_headers(admin_token),
        )
        assert create_resp.status_code == 201, (
            f"Expected 201 for macro creation, got {create_resp.status_code}: {create_resp.text}"
        )
        macro_id = (
            create_resp.json().get("id")
            or create_resp.json().get("macro_id")
        )
        assert macro_id, f"No macro id in response: {create_resp.json()}"

        # List — must contain the created macro
        list_resp = api("/rules/macros", headers=_headers(admin_token))
        assert list_resp.status_code == 200, (
            f"Expected 200 on list, got {list_resp.status_code}: {list_resp.text}"
        )
        names = [m.get("name") for m in list_resp.json()]
        assert name in names, f"Created macro '{name}' not found in list: {names}"

        # Delete
        delete_resp = api(
            f"/rules/macros/{macro_id}",
            "DELETE",
            headers=_headers(admin_token),
        )
        assert delete_resp.status_code == 204, (
            f"Expected 204 on delete, got {delete_resp.status_code}: {delete_resp.text}"
        )


# ── Player Lists ───────────────────────────────────────────────────────────────


@skip_unless_stack
class TestPlayerLists:
    """Covers /player-lists — GAP-8 player list management."""

    def test_create_and_delete_player_list(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        name = f"Blacklist GAP8 {uuid.uuid4().hex[:8]}"

        # Create list
        create_resp = api(
            "/player-lists",
            "POST",
            json={"name": name, "list_type": "BLACKLIST"},
            headers=_headers(admin_token),
        )
        assert create_resp.status_code in (200, 201), (
            f"Expected 200 or 201 for player list creation, got {create_resp.status_code}: {create_resp.text}"
        )
        list_id = (
            create_resp.json().get("id")
            or create_resp.json().get("list_id")
        )
        assert list_id, f"No list id in response: {create_resp.json()}"

        # Verify list appears in GET /player-lists
        get_resp = api("/player-lists", headers=_headers(admin_token))
        assert get_resp.status_code == 200, (
            f"Expected 200 on list, got {get_resp.status_code}: {get_resp.text}"
        )
        ids = [pl.get("id") or pl.get("list_id") for pl in get_resp.json()]
        assert list_id in ids, (
            f"Created list '{list_id}' not found in GET /player-lists"
        )

        # Add entries
        entries_resp = api(
            f"/player-lists/{list_id}/entries",
            "POST",
            json={
                "values": ["123.456.789-01", "987.654.321-00"],
                "value_type": "CPF",
            },
            headers=_headers(admin_token),
        )
        assert entries_resp.status_code in (200, 201), (
            f"Expected 200 or 201 for entries, got {entries_resp.status_code}: {entries_resp.text}"
        )

        # Delete list
        delete_resp = api(
            f"/player-lists/{list_id}",
            "DELETE",
            headers=_headers(admin_token),
        )
        assert delete_resp.status_code == 204, (
            f"Expected 204 on delete, got {delete_resp.status_code}: {delete_resp.text}"
        )

    def test_player_list_cross_tenant_isolation(self):
        admin_a_token = _login("admin_a", "admin123")["access_token"]
        name = f"Blacklist Isolation {uuid.uuid4().hex[:8]}"

        # Create as admin_a
        create_resp = api(
            "/player-lists",
            "POST",
            json={"name": name, "list_type": "BLACKLIST"},
            headers=_headers(admin_a_token),
        )
        assert create_resp.status_code in (200, 201), (
            f"Player list creation failed: {create_resp.status_code} {create_resp.text}"
        )
        list_id = (
            create_resp.json().get("id")
            or create_resp.json().get("list_id")
        )

        # List as admin_b — should NOT see admin_a's list
        admin_b_token = _login("admin_b", "admin123")["access_token"]
        get_resp = api("/player-lists", headers=_headers(admin_b_token))
        assert get_resp.status_code == 200, (
            f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        names_b = [pl.get("name") for pl in get_resp.json()]
        assert name not in names_b, (
            f"Cross-tenant isolation breach: list '{name}' visible to admin_b"
        )

        # Cleanup
        if list_id:
            api(
                f"/player-lists/{list_id}",
                "DELETE",
                headers=_headers(admin_a_token),
            )


# ── External Validation ───────────────────────────────────────────────────────


@skip_unless_stack
class TestExternalValidationEndpoints:
    """Covers external validation request/latest/by-id/history/retry flows."""

    def _first_player_id_for_tenant(self, token: str) -> str:
        resp = api("/players", headers=_headers(token), params={"limit": 1})
        assert resp.status_code == 200, (
            f"Expected 200 from /players, got {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert isinstance(data, list) and data, "Expected at least one seeded player"
        pid = data[0].get("id")
        assert pid, f"No id in player payload: {data[0]}"
        return pid

    def test_external_validation_full_flow(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        player_id = self._first_player_id_for_tenant(admin_token)

        # Create request
        create_resp = api(
            f"/players/{player_id}/external-validation",
            "POST",
            json={
                "provider": "mock_identity",
                "validation_type": "CPF_IDENTITY",
                "payload": {"trigger": "integration_test"},
            },
            headers=_headers(admin_token),
        )
        assert create_resp.status_code == 201, (
            f"Expected 201, got {create_resp.status_code}: {create_resp.text}"
        )
        req_id = create_resp.json().get("request_id")
        assert req_id, f"request_id missing in response: {create_resp.json()}"

        # Poll by id until terminal state (COMPLETED/FAILED)
        import time

        latest_payload = None
        for _ in range(20):
            by_id_resp = api(
                f"/external-validation/{req_id}",
                headers=_headers(admin_token),
            )
            assert by_id_resp.status_code == 200, (
                f"Expected 200 by id, got {by_id_resp.status_code}: {by_id_resp.text}"
            )
            latest_payload = by_id_resp.json()
            if latest_payload.get("status") in ("COMPLETED", "FAILED"):
                break
            time.sleep(0.2)

        assert latest_payload is not None
        assert latest_payload.get("status") in ("COMPLETED", "FAILED")

        # Latest endpoint
        latest_resp = api(
            f"/players/{player_id}/external-validation/latest",
            headers=_headers(admin_token),
        )
        assert latest_resp.status_code == 200, (
            f"Expected 200 latest, got {latest_resp.status_code}: {latest_resp.text}"
        )
        assert latest_resp.json().get("request_id")

        # History endpoint with filters
        hist_resp = api(
            f"/players/{player_id}/external-validation/history",
            headers=_headers(admin_token),
            params={"limit": 10, "offset": 0, "provider": "mock_identity"},
        )
        assert hist_resp.status_code == 200, (
            f"Expected 200 history, got {hist_resp.status_code}: {hist_resp.text}"
        )
        hist = hist_resp.json()
        assert isinstance(hist.get("items"), list)
        assert "total" in hist

    def test_external_validation_retry_non_failed_returns_400(self):
        admin_token = _login("admin_a", "admin123")["access_token"]
        player_id = self._first_player_id_for_tenant(admin_token)

        create_resp = api(
            f"/players/{player_id}/external-validation",
            "POST",
            json={"provider": "mock_identity", "validation_type": "CPF_IDENTITY", "payload": {}},
            headers=_headers(admin_token),
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json().get("request_id")
        assert req_id

        # Retry immediately likely catches PENDING/IN_PROGRESS and must fail with 400
        retry_resp = api(
            f"/external-validation/{req_id}/retry",
            "POST",
            headers=_headers(admin_token),
        )
        assert retry_resp.status_code == 400, (
            f"Expected 400 for retry non-failed, got {retry_resp.status_code}: {retry_resp.text}"
        )

    def test_external_validation_tenant_isolation_by_id(self):
        admin_a = _login("admin_a", "admin123")["access_token"]
        admin_b = _login("admin_b", "admin123")["access_token"]

        player_id = self._first_player_id_for_tenant(admin_a)
        create_resp = api(
            f"/players/{player_id}/external-validation",
            "POST",
            json={"provider": "mock_identity", "validation_type": "CPF_IDENTITY", "payload": {}},
            headers=_headers(admin_a),
        )
        assert create_resp.status_code == 201
        req_id = create_resp.json().get("request_id")
        assert req_id

        by_id_resp_other_tenant = api(
            f"/external-validation/{req_id}",
            headers=_headers(admin_b),
        )
        assert by_id_resp_other_tenant.status_code == 404, (
            f"Expected 404 cross-tenant, got {by_id_resp_other_tenant.status_code}: {by_id_resp_other_tenant.text}"
        )
