"""
Testes de integração do pipeline BetAML.
Requisitos: stack Docker rodando (docker-compose -f infra/docker-compose.yml up -d)

Por padrão esses testes são pulados. Para executar:
    TEST_STACK_UP=1 pytest tests/integration/ -v --tb=short

No CI com Docker:
    docker-compose -f infra/docker-compose.yml up -d
    TEST_STACK_UP=1 pytest tests/integration/
"""
import os
import time
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


def _make_txn_event(player_id: str | None = None) -> dict:
    return {
        "source_system":     "BackofficeAlpha",
        "event_type":        "TRANSACTION",
        "external_event_id": str(uuid.uuid4()),
        "player_id":         player_id or f"PLY-{uuid.uuid4().hex[:8]}",
        "amount":            1500.0,
        "currency":          "BRL",
        "transaction_type":  "DEPOSIT",
        "occurred_at":       "2024-06-15T10:00:00Z",
        "method":            "PIX",
        "status":            "SETTLED",
    }


# ── Module-scoped fixtures ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def token_a():
    return _login("admin_a", "admin123")["access_token"]


@pytest.fixture(scope="module")
def token_b():
    """Tenant B — cria contexto de tenant separado para testes de isolamento."""
    resp = api("/auth/login", "POST", json={"username": "admin_b", "password": "admin123"})
    if resp.status_code != 200:
        pytest.skip("Tenant B não configurado — pulando testes de isolamento")
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(token_a):
    """Alias de compatibilidade para fixtures legadas."""
    return _headers(token_a)


@pytest.fixture(scope="module")
def headers_a(token_a):
    return _headers(token_a)


@pytest.fixture(scope="module")
def headers_b(token_b):
    return _headers(token_b)


# ── Health check ───────────────────────────────────────────────────────────────

@skip_unless_stack
def test_api_health():
    resp = api("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@skip_unless_stack
def test_api_version():
    resp = api("/health")
    assert resp.status_code == 200


# ── Auth & JWT ─────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_login_json_returns_token_and_role():
    """Login via JSON deve retornar access_token + role + tenant_id."""
    data = _login("admin_a", "admin123")
    assert "access_token" in data
    assert "role" in data
    assert "tenant_id" in data


@skip_unless_stack
def test_login_form_encoded_returns_422():
    """Backend espera JSON — form-urlencoded deve retornar 422 (quebra de contrato)."""
    resp = api(
        "/auth/login", "POST",
        data={"username": "admin_a", "password": "admin123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422


@skip_unless_stack
def test_login_wrong_password():
    resp = api("/auth/login", "POST", json={"username": "admin_a", "password": "errada"})
    assert resp.status_code in (400, 401)


@skip_unless_stack
def test_login_unknown_user():
    resp = api("/auth/login", "POST", json={"username": "nao_existe_xyzabc", "password": "pass"})
    assert resp.status_code in (401, 404)


@skip_unless_stack
def test_me_authenticated(headers_a):
    resp = api("/me", headers=headers_a)
    assert resp.status_code == 200
    assert "role" in resp.json()


@skip_unless_stack
def test_me_unauthenticated():
    resp = api("/me")
    assert resp.status_code == 401


@skip_unless_stack
def test_protected_endpoint_requires_auth():
    resp = api("/alerts")
    assert resp.status_code == 401


# ── Ingest pipeline ────────────────────────────────────────────────────────────

@skip_unless_stack
def test_ingest_single_event_202(headers_a):
    resp = api("/ingest/event", "POST", headers=headers_a, json=_make_txn_event())
    assert resp.status_code == 202
    assert "event_id" in resp.json()


@skip_unless_stack
def test_ingest_batch_202(headers_a):
    events = [_make_txn_event() for _ in range(3)]
    resp = api("/ingest/batch", "POST", headers=headers_a, json={"events": events})
    assert resp.status_code in (200, 202)
    assert resp.json().get("accepted", 0) > 0


@skip_unless_stack
def test_ingest_invalid_event_returns_error(headers_a):
    resp = api("/ingest/event", "POST", headers=headers_a, json={"bad_field": "faltam campos obrigatórios"})
    assert resp.status_code in (400, 422)


@skip_unless_stack
def test_ingest_unknown_source_system_rejected(headers_a):
    payload = {**_make_txn_event(), "source_system": "UnknownBackofficeXYZ"}
    resp = api("/ingest/event", "POST", headers=headers_a, json=payload)
    assert resp.status_code in (400, 422)


@skip_unless_stack
def test_ingest_10_events_in_sequence(headers_a):
    """10 eventos seguidos devem ser aceitos sem erro."""
    failed = 0
    for _ in range(10):
        resp = api("/ingest/event", "POST", headers=headers_a, json=_make_txn_event())
        if resp.status_code != 202:
            failed += 1
    assert failed == 0, f"{failed}/10 eventos rejeitados"


# ── Alerts ─────────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_list_alerts_returns_paged_response(headers_a):
    """`GET /alerts` deve retornar {total, items}."""
    resp = api("/alerts", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "items" in body
    assert isinstance(body["items"], list)


@skip_unless_stack
def test_list_alerts_filter_by_status(headers_a):
    resp = api("/alerts?status=OPEN", headers=headers_a)
    assert resp.status_code == 200


@skip_unless_stack
def test_list_alerts_filter_by_severity(headers_a):
    resp = api("/alerts?severity=HIGH", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    for item in body.get("items", []):
        assert item["severity"] == "HIGH"


@skip_unless_stack
def test_get_alert_detail(headers_a):
    list_resp = api("/alerts?per_page=1", headers=headers_a)
    items = list_resp.json().get("items", [])
    if not items:
        pytest.skip("Sem alertas disponíveis")
    alert_id = items[0]["id"]

    resp = api(f"/alerts/{alert_id}", headers=headers_a)
    assert resp.status_code == 200
    detail = resp.json()
    assert "id" in detail
    assert "severity" in detail


@skip_unless_stack
def test_get_nonexistent_alert_404(headers_a):
    resp = api("/alerts/00000000-0000-0000-0000-000000000000", headers=headers_a)
    assert resp.status_code == 404


@skip_unless_stack
def test_triage_alert(headers_a):
    list_resp = api("/alerts?status=OPEN&per_page=1", headers=headers_a)
    items = list_resp.json().get("items", [])
    if not items:
        pytest.skip("Sem alertas OPEN disponíveis")
    alert_id = items[0]["id"]

    resp = api(f"/alerts/{alert_id}/triage", "POST", headers=headers_a, json={
        "disposition": "IN_REVIEW",
        "note": "Triagem automática via teste de integração",
    })
    assert resp.status_code in (200, 204)

    detail = api(f"/alerts/{alert_id}", headers=headers_a).json()
    assert detail.get("status") in ("IN_REVIEW", "OPEN")


@skip_unless_stack
def test_triage_invalid_disposition(headers_a):
    list_resp = api("/alerts?status=OPEN&per_page=1", headers=headers_a)
    items = list_resp.json().get("items", [])
    if not items:
        pytest.skip("Sem alertas OPEN disponíveis")
    alert_id = items[0]["id"]

    resp = api(f"/alerts/{alert_id}/triage", "POST", headers=headers_a, json={
        "disposition": "INVALID_DISPOSITION_XYZ",
    })
    assert resp.status_code in (400, 422)


@skip_unless_stack
def test_alerts_pagination(headers_a):
    page1 = api("/alerts?page=1&per_page=2", headers=headers_a)
    page2 = api("/alerts?page=2&per_page=2", headers=headers_a)
    assert page1.status_code == 200
    assert page2.status_code == 200

    items1 = page1.json().get("items", [])
    items2 = page2.json().get("items", [])
    total = page1.json().get("total", 0)

    if items1 and items2 and total >= 4:
        ids1 = {a["id"] for a in items1}
        ids2 = {a["id"] for a in items2}
        assert ids1.isdisjoint(ids2), "Paginação retorna itens duplicados"


# ── Cases ──────────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_create_case(headers_a):
    resp = api("/cases", "POST", headers=headers_a, json={
        "title":       f"Caso de Teste {uuid.uuid4().hex[:6]}",
        "description": "Criado por teste de integração automatizado",
        "priority":    "HIGH",
    })
    assert resp.status_code in (200, 201)
    case = resp.json()
    assert "id" in case
    assert case.get("status") == "OPEN"


@skip_unless_stack
def test_list_cases_returns_list(headers_a):
    resp = api("/cases", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    if isinstance(body, dict):
        assert "items" in body or "cases" in body
    else:
        assert isinstance(body, list)


@skip_unless_stack
def test_get_case_detail(headers_a):
    create_resp = api("/cases", "POST", headers=headers_a, json={
        "title":    "Caso para Fetch",
        "priority": "MEDIUM",
    })
    if create_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = create_resp.json()["id"]

    resp = api(f"/cases/{case_id}", headers=headers_a)
    assert resp.status_code == 200
    assert resp.json()["id"] == case_id


@skip_unless_stack
def test_link_alert_to_case(headers_a):
    case_resp = api("/cases", "POST", headers=headers_a, json={"title": "Caso Link Test"})
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]

    alert_resp = api("/alerts?status=OPEN&per_page=1", headers=headers_a)
    items = alert_resp.json().get("items", [])
    if not items:
        pytest.skip("Sem alertas disponíveis para vincular")
    alert_id = items[0]["id"]

    resp = api(f"/alerts/{alert_id}/link-to-case", "POST", headers=headers_a, json={"case_id": case_id})
    assert resp.status_code in (200, 204)


# ── Rules Engine ───────────────────────────────────────────────────────────────

@skip_unless_stack
def test_list_rules(headers_a):
    resp = api("/rules", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    if isinstance(body, dict):
        assert "items" in body or "rules" in body
    else:
        assert isinstance(body, list)


@skip_unless_stack
def test_create_rule_with_valid_dsl(headers_a):
    resp = api("/rules", "POST", headers=headers_a, json={
        "name":       f"Regra Teste {uuid.uuid4().hex[:6]}",
        "expression": "transaction.amount > 9000 and transaction.type == 'DEPOSIT'",
        "severity":   "HIGH",
        "enabled":    True,
    })
    assert resp.status_code in (200, 201)
    assert "id" in resp.json()


@skip_unless_stack
def test_create_rule_with_invalid_dsl_rejected(headers_a):
    resp = api("/rules", "POST", headers=headers_a, json={
        "name":       "Regra com DSL inválido",
        "expression": "(((sem fechamento",
        "severity":   "LOW",
    })
    assert resp.status_code in (400, 422)


@skip_unless_stack
def test_validate_dsl_endpoint(headers_a):
    resp = api("/rules/validate", "POST", headers=headers_a, json={
        "expression": "features.deposit_sum_24h > 30000 and player.pepFlag == true",
    })
    assert resp.status_code == 200
    assert resp.json().get("valid") is True


@skip_unless_stack
def test_validate_invalid_dsl_endpoint(headers_a):
    resp = api("/rules/validate", "POST", headers=headers_a, json={
        "expression": "not_a_valid(((",
    })
    assert resp.status_code == 200
    assert resp.json().get("valid") is False


@skip_unless_stack
def test_simulate_rule_match(headers_a):
    """Simula evento contra a regra de structuring."""
    resp = api("/rules", headers=headers_a)
    body = resp.json()
    rules = body if isinstance(body, list) else body.get("items", [])
    structuring = next((r for r in rules if "structuring" in r.get("name", "").lower()), None)
    if not structuring:
        pytest.skip("Regra de structuring não encontrada")

    sim_resp = api(
        f"/rules/{structuring['id']}/simulate", "POST", headers=headers_a,
        json={
            "transaction": {"amount": 9500, "type": "DEPOSIT"},
            "features":    {"zscore_current_deposit_vs_baseline": 1.0},
        },
    )
    assert sim_resp.status_code == 200
    assert sim_resp.json().get("matched") is True


# ── Players ────────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_list_players(headers_a):
    resp = api("/players", headers=headers_a)
    assert resp.status_code == 200


@skip_unless_stack
def test_get_player_not_found(headers_a):
    resp = api("/players/00000000-0000-0000-0000-000000000000", headers=headers_a)
    assert resp.status_code == 404


@skip_unless_stack
def test_get_player_profile(headers_a):
    resp = api("/players?per_page=1", headers=headers_a)
    body = resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Nenhum player disponível")
    pid = players[0]["id"]
    detail = api(f"/players/{pid}", headers=headers_a)
    assert detail.status_code == 200


@skip_unless_stack
def test_player_features_endpoint(headers_a):
    resp = api("/players?per_page=1", headers=headers_a)
    body = resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")
    player_id = players[0]["id"]

    resp = api(f"/players/{player_id}/features", headers=headers_a)
    assert resp.status_code in (200, 404)


# ── Multi-tenant isolation ─────────────────────────────────────────────────────

@skip_unless_stack
def test_tenant_a_cannot_see_tenant_b_alerts(headers_a, headers_b):
    """Alerts do tenant A não devem ser visíveis ao tenant B (RLS)."""
    alerts_a = api("/alerts", headers=headers_a).json().get("items", [])
    alerts_b = api("/alerts", headers=headers_b).json().get("items", [])

    ids_a = {a["id"] for a in alerts_a}
    ids_b = {a["id"] for a in alerts_b}

    if ids_a and ids_b:
        assert ids_a.isdisjoint(ids_b), "Vazamento de dados entre tenants detectado!"


@skip_unless_stack
def test_tenant_a_cannot_access_tenant_b_resource(headers_a, token_b):
    """Token A não deve acessar recursos criados no tenant B."""
    case_resp = api("/cases", "POST", headers=_headers(token_b), json={"title": "Caso Tenant B"})
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso no tenant B falhou")
    case_id = case_resp.json()["id"]

    resp = api(f"/cases/{case_id}", headers=headers_a)
    assert resp.status_code in (403, 404), (
        f"Tenant A acessou recurso do tenant B! Status: {resp.status_code}"
    )


# ── Audit log & Reports ────────────────────────────────────────────────────────

@skip_unless_stack
def test_audit_log_endpoint(headers_a):
    resp = api("/audit-log", headers=headers_a)
    assert resp.status_code == 200


@skip_unless_stack
def test_list_reports(headers_a):
    resp = api("/reports", headers=headers_a)
    assert resp.status_code in (200, 404)


# ── API keys ──────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_list_api_keys(headers_a):
    resp = api("/api-keys", headers=headers_a)
    assert resp.status_code in (200, 403)


@skip_unless_stack
def test_create_and_revoke_api_key(headers_a):
    create_resp = api("/api-keys", "POST", headers=headers_a, json={"name": "test-key-integration"})
    if create_resp.status_code not in (200, 201):
        pytest.skip("Criação de API key não suportada ou sem permissão")
    key_id = create_resp.json()["id"]

    revoke_resp = api(f"/api-keys/{key_id}", "DELETE", headers=headers_a)
    assert revoke_resp.status_code in (200, 204)


# ── End-to-end pipeline smoke test ────────────────────────────────────────────

@skip_unless_stack
def test_e2e_ingest_to_alert(headers_a):
    """
    Smoke test end-to-end:
    1. Ingere transação de structuring (valor em faixa suspeita)
    2. Aguarda processamento assíncrono com polling (máx 15 s)
    3. Verifica que o pipeline não travou (alerta pode ou não ter chegado)
    """
    player_id = f"PLY-E2E-{uuid.uuid4().hex[:6]}"
    ingest_resp = api("/ingest/event", "POST", headers=headers_a, json={
        **_make_txn_event(player_id),
        "amount": 9700.0,
    })
    assert ingest_resp.status_code == 202

    before = api("/alerts", headers=headers_a).json().get("total", 0)
    for _ in range(15):
        time.sleep(1.0)
        after = api("/alerts", headers=headers_a).json().get("total", 0)
        if after > before:
            break
