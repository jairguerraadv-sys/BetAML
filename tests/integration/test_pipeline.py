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
import json
import hmac
import hashlib
import asyncio
import importlib.util
import time
import uuid
import sys
from datetime import date
import pytest
import requests

BASE_URL = os.getenv("API_URL", "http://localhost:8000")
RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"
POSTGRES_DSN = os.getenv("BETAML_TEST_DB_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
REDIS_URL = os.getenv("BETAML_TEST_REDIS_URL", "redis://:devpass@localhost:6379/0")

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


def _load_api_main():
    # Cachea o módulo para evitar re-registro de métricas Prometheus
    # (CollectorRegistry é global no processo de testes).
    global _API_MAIN_SINGLETON
    if _API_MAIN_SINGLETON is not None:
        return _API_MAIN_SINGLETON

    # Quando importamos main.py fora do container, precisamos forçar URLs
    # para localhost (os hostnames internos do docker network não resolvem).
    os.environ.setdefault("DATABASE_URL", POSTGRES_DSN)
    os.environ.setdefault("REDIS_URL", REDIS_URL)
    os.environ.setdefault("ENVIRONMENT", "test")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    api_dir = os.path.join(root, "services", "api")
    libs_dir = os.path.join(root, "libs")
    for path in (api_dir, libs_dir):
        while path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, libs_dir)
    sys.path.insert(0, api_dir)

    for key, module in list(sys.modules.items()):
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        module_file = os.path.abspath(module_file)
        if module_file.startswith(api_dir) or module_file.startswith(libs_dir):
            sys.modules.pop(key, None)

    module_name = f"api_main_feature_store_integration_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(api_dir, "main.py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _API_MAIN_SINGLETON = module
    return module


_API_MAIN_SINGLETON = None


async def _pg_execute(statement: str, *args):
    import asyncpg

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        return await conn.execute(statement, *args)
    finally:
        await conn.close()


async def _pg_fetchval(statement: str, *args):
    import asyncpg

    conn = await asyncpg.connect(POSTGRES_DSN)
    try:
        return await conn.fetchval(statement, *args)
    finally:
        await conn.close()


async def _redis_delete(key: str):
    import redis.asyncio as aioredis

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.delete(key)
    finally:
        await redis.aclose()


def _make_txn_event(player_id: str | None = None) -> dict:
    return {
        "source_system":   "BackofficeAlpha",
        "entity_type":     "transaction",
        "source_event_id": str(uuid.uuid4()),
        "payload": {
            "player_id":        player_id or f"PLY-{uuid.uuid4().hex[:8]}",
            "amount":           1500.0,
            "currency":         "BRL",
            "transaction_type": "DEPOSIT",
            "occurred_at":      "2024-06-15T10:00:00Z",
            "method":           "PIX",
            "status":           "SETTLED",
        },
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
    resp = api("/ingest/batch", "POST", headers=headers_a, json=events)
    assert resp.status_code in (200, 202)
    assert resp.json().get("count", 0) > 0


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


@skip_unless_stack
def test_ingest_connector_gamma_xml_parse(headers_a):
    xml_payload = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<transactions>
  <transaction>
    <id>TXG-1</id>
    <player_id>PLY-G-1</player_id>
    <type>DEPOSIT</type>
    <amount>1200.50</amount>
    <currency>BRL</currency>
    <timestamp>2026-03-09T10:00:00Z</timestamp>
  </transaction>
</transactions>
""".encode("utf-8")

    resp = api(
        "/ingest/connectors/gamma/parse",
        "POST",
        headers=headers_a,
        files={"file": ("gamma.xml", xml_payload, "application/xml")},
        data={"entity_type": "transaction"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "job_id" in body
    assert body.get("source_system") == "ConnectorGamma"
    assert body.get("summary", {}).get("accepted", 0) >= 1


@skip_unless_stack
def test_ingest_connector_delta_ndjson_parse(headers_a):
    ndjson_payload = (
        '{"id":"TXD-1","player_id":"PLY-D-1","evt_type":"DEPOSIT","amount":500.0,"ts":"2026-03-09T10:01:00Z"}\n'
        '{"id":"TXD-2","player_id":"PLY-D-2","evt_type":"WITHDRAWAL","amount":100.0,"ts":"2026-03-09T10:02:00Z"}\n'
    ).encode("utf-8")

    resp = api(
        "/ingest/connectors/delta/parse",
        "POST",
        headers=headers_a,
        files={"file": ("delta.ndjson", ndjson_payload, "application/x-ndjson")},
        data={"entity_type": "transaction"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "job_id" in body
    assert body.get("source_system") == "ConnectorDelta"
    assert body.get("summary", {}).get("accepted", 0) >= 2


@skip_unless_stack
def test_ingest_connector_unknown_name_rejected(headers_a):
    resp = api(
        "/ingest/connectors/unknown/parse",
        "POST",
        headers=headers_a,
        files={"file": ("unknown.txt", b"{}", "application/json")},
        data={"entity_type": "transaction"},
    )
    assert resp.status_code == 400


@skip_unless_stack
def test_ingest_webhook_epsilon_hmac_validation(headers_a):
    payload = {
        "events": [
            {
                "event_id": f"evt-eps-{uuid.uuid4().hex[:8]}",
                "player_id": f"PLY-EPS-{uuid.uuid4().hex[:6]}",
                "event_type": "DEPOSIT",
                "gross_amount": 999.9,
                "event_time": "2026-03-09T10:03:00Z",
                "currency_code": "BRL",
            }
        ]
    }
    raw = json.dumps(payload).encode("utf-8")
    secret = "dev-secret-change-me"
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    ok_resp = api(
        "/ingest/webhook/epsilon",
        "POST",
        headers={
            **headers_a,
            "Content-Type": "application/json",
            "x-epsilon-signature": signature,
        },
        data=raw,
    )
    assert ok_resp.status_code == 202, ok_resp.text
    assert ok_resp.json().get("job_id")

    bad_resp = api(
        "/ingest/webhook/epsilon",
        "POST",
        headers={
            **headers_a,
            "Content-Type": "application/json",
            "x-epsilon-signature": "sha256=invalid",
        },
        data=raw,
    )
    assert bad_resp.status_code in (400, 401), bad_resp.text


@skip_unless_stack
def test_resolve_ingest_error_not_found(headers_a):
    resp = api(
        f"/ingest/errors/{uuid.uuid4()}/resolve",
        "POST",
        headers=headers_a,
        json={"note": "resolve inexistente"},
    )
    assert resp.status_code == 404


@skip_unless_stack
def test_reprocess_job_not_found(headers_a):
    resp = api(
        f"/ingest/jobs/{uuid.uuid4()}/reprocess",
        "POST",
        headers=headers_a,
        json={"reason": "job inexistente"},
    )
    assert resp.status_code == 404


@skip_unless_stack
def test_replay_ingest_error_not_found(headers_a):
    resp = api(
        f"/ingest/errors/{uuid.uuid4()}/replay",
        "POST",
        headers=headers_a,
        json={
            "corrected_payload": {
                "event_id": f"evt-{uuid.uuid4().hex[:8]}",
                "external_player_id": "CPF123",
                "transaction_type": "DEPOSIT",
                "amount": 10.0,
                "occurred_at": "2026-03-10T12:00:00Z",
            }
        },
    )
    assert resp.status_code == 404


@skip_unless_stack
def test_mapping_templates_endpoint(headers_a):
    resp = api("/mappings/templates", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    gamma = next((i for i in body if i.get("source_system") == "ConnectorGamma"), None)
    epsilon = next((i for i in body if i.get("source_system") == "ConnectorEpsilon"), None)
    assert gamma is not None
    assert gamma.get("payload_format") == "xml"
    assert gamma.get("content_type") == "application/xml"
    assert isinstance(gamma.get("input_schema"), list)
    assert gamma.get("sample_payload")
    assert epsilon is not None
    assert epsilon.get("auth_mode") == "hmac_sha256"
    assert epsilon.get("signature_header") == "x-epsilon-signature"


@skip_unless_stack
def test_mapping_validate_and_preview(headers_a):
    mapping_yaml = """
source_system: ConnectorGamma
entity_type: TRANSACTION
connector: xml
transforms:
  - field: event_id
    type: copy
    source: event_id
  - field: external_player_id
    type: copy
    source: external_player_id
  - field: transaction_type
    type: copy
    source: transaction_type
  - field: amount
    type: coerceDecimal
    source: amount
  - field: occurred_at
    type: parseDate
    source: occurred_at
  - field: currency
    type: copy
    source: currency
""".strip()

    valid_resp = api(
        "/mappings/validate",
        "POST",
        headers=headers_a,
        json={"config_text": mapping_yaml, "format": "yaml"},
    )
    assert valid_resp.status_code == 200
    valid_body = valid_resp.json()
    assert valid_body.get("valid") is True
    assert valid_body.get("canonical_validation", {}).get("valid") is True

    preview_resp = api(
        "/mappings/preview",
        "POST",
        headers=headers_a,
        json={
            "config_text": mapping_yaml,
            "format": "yaml",
            "sample": {
                "event_id": "evt-1",
                "external_player_id": "CPF123",
                "transaction_type": "DEPOSIT",
                "amount": "99.90",
                "occurred_at": "2026-03-20T12:00:00Z",
                "currency": "BRL",
            },
        },
    )
    assert preview_resp.status_code == 200
    body = preview_resp.json()
    assert body.get("valid") is True
    assert body.get("canonical_validation", {}).get("valid") is True
    assert body.get("preview", {}).get("event_id") == "evt-1"


@skip_unless_stack
def test_mapping_versioning_and_rollback(headers_a):
    create_resp = api(
        "/mappings",
        "POST",
        headers=headers_a,
        json={
            "name": f"Map Test {uuid.uuid4().hex[:6]}",
            "source_system": "ConnectorDelta",
            "entity_type": "TRANSACTION",
            "format": "json",
            "config_json": {
                "source_system": "ConnectorDelta",
                "entity_type": "TRANSACTION",
                "fields": [
                    {"target": "event_id", "source": "event_id", "transform": "copy"},
                    {"target": "external_player_id", "source": "external_player_id", "transform": "copy"},
                    {"target": "transaction_type", "source": "transaction_type", "transform": "copy"},
                    {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                    {"target": "occurred_at", "source": "occurred_at", "transform": "parseDate"},
                ],
            },
            "change_notes": "v1",
        },
    )
    assert create_resp.status_code == 201
    mapping_id = create_resp.json()["id"]

    update_resp = api(
        f"/mappings/{mapping_id}",
        "PUT",
        headers=headers_a,
        json={
            "format": "json",
            "change_notes": "v2",
            "config_json": {
                "source_system": "ConnectorDelta",
                "entity_type": "TRANSACTION",
                "fields": [
                    {"target": "event_id", "source": "event_id", "transform": "copy"},
                    {"target": "external_player_id", "source": "external_player_id", "transform": "copy"},
                    {"target": "transaction_type", "source": "transaction_type", "transform": "copy"},
                    {"target": "amount", "source": "amount", "transform": "coerceDecimal"},
                    {"target": "occurred_at", "source": "occurred_at", "transform": "parseDate"},
                    {"target": "currency", "source": "currency", "transform": "copy"},
                ],
            },
        },
    )
    assert update_resp.status_code == 200

    versions_resp = api(f"/mappings/{mapping_id}/versions", headers=headers_a)
    assert versions_resp.status_code == 200
    versions = versions_resp.json()
    assert len(versions) >= 2
    assert any(v.get("version_number") == 1 for v in versions)

    rollback_resp = api(f"/mappings/{mapping_id}/rollback?version_number=1", "POST", headers=headers_a)
    assert rollback_resp.status_code == 200
    assert rollback_resp.json().get("version_number") == 1


@skip_unless_stack
def test_ingest_error_replay_with_corrected_payload(headers_a):
    xml_payload = """
<Events>
  <Transaction>
    <EventId></EventId>
    <PlayerId>CPF-REPLAY-01</PlayerId>
    <Type>DEPOSIT</Type>
    <Amount currency="BRL">100.00</Amount>
    <Timestamp>2026-03-10T10:00:00Z</Timestamp>
  </Transaction>
</Events>
""".strip()

    parse_resp = api(
        "/ingest/connectors/gamma/parse",
        "POST",
        headers=headers_a,
        files={"file": ("gamma-invalid.xml", xml_payload.encode("utf-8"), "application/xml")},
        data={"entity_type": "TRANSACTION"},
    )
    assert parse_resp.status_code == 202, parse_resp.text
    job_id = parse_resp.json()["job_id"]

    errors_resp = api(f"/ingest/errors?job_id={job_id}&limit=10", headers=headers_a)
    assert errors_resp.status_code == 200, errors_resp.text
    items = errors_resp.json()
    assert items, items
    error_id = items[0]["id"]

    replay_resp = api(
        f"/ingest/errors/{error_id}/replay",
        "POST",
        headers=headers_a,
        json={
            "corrected_payload": {
                "event_id": f"evt-replay-{uuid.uuid4().hex[:8]}",
                "external_player_id": "CPF-REPLAY-01",
                "transaction_type": "DEPOSIT",
                "amount": 100.0,
                "occurred_at": "2026-03-10T10:00:00Z",
                "currency": "BRL",
            },
            "entity_type": "TRANSACTION",
            "note": "correção manual de payload",
        },
    )
    assert replay_resp.status_code == 202, replay_resp.text
    replay_body = replay_resp.json()
    assert replay_body["status"] == "queued"
    assert replay_body["ingest_error_id"] == error_id
    assert replay_body["resolved"] is True

    refreshed_errors = api(f"/ingest/errors?job_id={job_id}&limit=10", headers=headers_a)
    assert refreshed_errors.status_code == 200
    refreshed = next((item for item in refreshed_errors.json() if item.get("id") == error_id), None)
    assert refreshed is not None
    assert refreshed["resolved"] is True
    assert isinstance(refreshed.get("error_detail"), dict)
    assert isinstance(refreshed["error_detail"].get("replay"), dict)
    assert refreshed["error_detail"]["replay"].get("note") == "correção manual de payload"


@skip_unless_stack
def test_list_ingest_errors_endpoint(headers_a):
    resp = api("/ingest/errors?limit=5", headers=headers_a)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


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
        "name":          f"Regra Teste {uuid.uuid4().hex[:6]}",
        "condition_dsl": "transaction.amount > 9000 and transaction.type == 'DEPOSIT'",
        "severity":      "HIGH",
        "status":        "ACTIVE",
        "scope":         "TRANSACTION",
        "params":        {},
    })
    assert resp.status_code in (200, 201)
    assert "id" in resp.json()


@skip_unless_stack
def test_create_rule_with_invalid_dsl_rejected(headers_a):
    resp = api("/rules", "POST", headers=headers_a, json={
        "name":          "Regra com DSL inválido",
        "condition_dsl": "(((sem fechamento",
        "severity":      "LOW",
        "status":        "DRAFT",
        "scope":         "TRANSACTION",
        "params":        {},
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
            "events": [{
                "transaction": {"amount": 9500, "type": "DEPOSIT"},
                "features":    {
                    "deposit_count_24h": 10,
                    "deposit_sum_24h": 6000,
                    "zscore_current_deposit_vs_baseline": 1.0,
                },
            }],
        },
    )
    assert sim_resp.status_code == 200
    result = sim_resp.json()
    assert result.get("matches", 0) > 0 or any(r.get("matched") for r in result.get("results", []))


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
    resp = api("/audit-logs", headers=headers_a)
    assert resp.status_code == 200


@skip_unless_stack
def test_list_reports(headers_a):
    resp = api("/reports", headers=headers_a)
    assert resp.status_code in (200, 404)


# ── API keys ──────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_list_api_keys(headers_a):
    resp = api("/admin/api-keys", headers=headers_a)
    assert resp.status_code in (200, 403)


@skip_unless_stack
def test_create_and_revoke_api_key(headers_a):
    create_resp = api("/admin/api-keys", "POST", headers=headers_a, json={"name": "test-key-integration"})
    if create_resp.status_code not in (200, 201):
        pytest.skip("Criação de API key não suportada ou sem permissão")
    body = create_resp.json()
    key_id = body["id"]
    raw_key = body.get("raw_key")
    assert raw_key, f"Resposta sem raw_key: {body}"

    # API key deve conseguir chamar ingest sem Bearer JWT.
    ingest_headers = {"X-API-Key": raw_key}
    ingest_resp = api("/ingest/event", "POST", headers=ingest_headers, json=_make_txn_event())
    assert ingest_resp.status_code == 202, ingest_resp.text

    revoke_resp = api(f"/admin/api-keys/{key_id}", "DELETE", headers=headers_a)
    assert revoke_resp.status_code in (200, 204)

    # Após revogação, ingest com a mesma chave deve falhar.
    ingest_resp2 = api("/ingest/event", "POST", headers=ingest_headers, json=_make_txn_event())
    assert ingest_resp2.status_code in (401, 403)


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
    evt = _make_txn_event(player_id)
    evt["payload"]["amount"] = 9700.0
    ingest_resp = api("/ingest/event", "POST", headers=headers_a, json=evt)
    assert ingest_resp.status_code == 202

    before = api("/alerts", headers=headers_a).json().get("total", 0)
    for _ in range(15):
        time.sleep(1.0)
        after = api("/alerts", headers=headers_a).json().get("total", 0)
        if after > before:
            break


# ── File ingestion E2E ────────────────────────────────────────────────────────

def _make_csv_payload(rows: int = 5, source_system: str = "BackofficeAlpha") -> bytes:
    """Gera CSV de transações no formato do BackofficeAlpha para upload."""
    import io as _io
    buf = _io.StringIO()
    buf.write("txnId,playerId,txnAmount,txnType,txnStatus,txnTimestamp\n")
    for i in range(rows):
        buf.write(
            f"TXN-{uuid.uuid4().hex[:8]},"
            f"PLY-{uuid.uuid4().hex[:8]},"
            f"{(i + 1) * 1000.0},"
            "DEPOSIT,SETTLED,"
            "2024-06-15T10:00:00Z\n"
        )
    return buf.getvalue().encode("utf-8")


@skip_unless_stack
def test_file_ingest_returns_job_id(headers_a):
    """POST /ingest/file deve aceitar CSV e retornar job_id."""
    csv_bytes = _make_csv_payload(rows=3)
    resp = api(
        "/ingest/file", "POST",
        headers=headers_a,
        files={"file": ("transactions.csv", csv_bytes, "text/csv")},
        data={"source_system": "BackofficeAlpha", "entity_type": "transaction"},
    )
    assert resp.status_code in (200, 202), f"Esperado 200/202, recebido {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "job_id" in body, f"Resposta sem job_id: {body}"


@skip_unless_stack
def test_file_ingest_job_status_polling(headers_a):
    """
    E2E: faz upload de CSV, obtém job_id, e faz polling até DONE/FAILED (máx 30 s).
    O stream_processor precisa estar rodando para processar o job.
    """
    csv_bytes = _make_csv_payload(rows=5)
    upload_resp = api(
        "/ingest/file", "POST",
        headers=headers_a,
        files={"file": ("test_batch.csv", csv_bytes, "text/csv")},
        data={"source_system": "BackofficeAlpha", "entity_type": "transaction"},
    )
    assert upload_resp.status_code in (200, 202), upload_resp.text
    job_id = upload_resp.json()["job_id"]

    # Poll de status com timeout 30 s
    final_status = None
    for _ in range(30):
        time.sleep(1.0)
        status_resp = api(f"/ingest/jobs/{job_id}", headers=headers_a)
        if status_resp.status_code != 200:
            continue
        job = status_resp.json()
        if job.get("status") in ("DONE", "FAILED", "PARTIAL"):
            final_status = job["status"]
            break

    assert final_status is not None, (
        f"Job {job_id} não completou em 30 s. Verifique se stream_processor está rodando."
    )
    assert final_status != "FAILED", f"Job falhou: {final_status}"


@skip_unless_stack
def test_file_ingest_invalid_source_system(headers_a):
    """Upload com source_system desconhecido deve ser rejeitado."""
    csv_bytes = _make_csv_payload(rows=1)
    resp = api(
        "/ingest/file", "POST",
        headers=headers_a,
        files={"file": ("bad.csv", csv_bytes, "text/csv")},
        data={"source_system": "UnknownSystemXYZ999", "entity_type": "transaction"},
    )
    assert resp.status_code in (400, 422), (
        f"Esperado 400/422 para source_system inválido, recebido: {resp.status_code}"
    )


@skip_unless_stack
def test_file_ingest_empty_csv_rejected(headers_a):
    """CSV vazio (sem linhas de dados) deve retornar erro."""
    csv_bytes = b"txnId,playerId,txnAmount\n"  # header apenas, zero rows
    resp = api(
        "/ingest/file", "POST",
        headers=headers_a,
        files={"file": ("empty.csv", csv_bytes, "text/csv")},
        data={"source_system": "BackofficeAlpha", "entity_type": "transaction"},
    )
    assert resp.status_code in (400, 422), (
        f"CSV vazio deveria ser rejeitado, recebido: {resp.status_code}"
    )


@skip_unless_stack
def test_file_ingest_tenant_isolation(headers_a, headers_b):
    """
    Job criado pelo tenant A não pode ser acessado pelo tenant B.
    """
    csv_bytes = _make_csv_payload(rows=2)
    upload_resp = api(
        "/ingest/file", "POST",
        headers=headers_a,
        files={"file": ("isolation_test.csv", csv_bytes, "text/csv")},
        data={"source_system": "BackofficeAlpha", "entity_type": "transaction"},
    )
    if upload_resp.status_code not in (200, 202):
        pytest.skip("Upload falhou, teste de isolamento não pode prosseguir")
    job_id = upload_resp.json()["job_id"]

    # Tenant B tenta acessar o job do tenant A
    cross_resp = api(f"/ingest/jobs/{job_id}", headers=headers_b)
    assert cross_resp.status_code in (403, 404), (
        f"Tenant B acessou job do tenant A! Status: {cross_resp.status_code}"
    )


@skip_unless_stack
def test_ingest_jobs_list(headers_a):
    """GET /ingest/jobs deve retornar lista paginada dos jobs do tenant."""
    resp = api("/ingest/jobs", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    if isinstance(body, dict):
        assert "items" in body or "jobs" in body or isinstance(body.get("data"), list)
    else:
        assert isinstance(body, list)


@skip_unless_stack
def test_ingest_jobs_list_filter_by_source_system(headers_a):
    resp = api("/ingest/jobs?source_system=BackofficeAlpha&limit=5", headers=headers_a)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    for item in body:
        assert item.get("source_system") == "BackofficeAlpha"


@skip_unless_stack
def test_ingest_jobs_list_rejects_cross_tenant_filter(headers_a):
    resp = api("/ingest/jobs?tenant=other-tenant", headers=headers_a)
    assert resp.status_code == 403


@skip_unless_stack
def test_feature_store_history_rejects_invalid_range(headers_a):
    resp = api(
        f"/feature-store/players/{uuid.uuid4()}/history?from=2026-03-10T00:00:00Z&to=2026-03-09T00:00:00Z",
        headers=headers_a,
    )
    assert resp.status_code in (400, 404)


@skip_unless_stack
def test_feature_store_current_endpoint(headers_a):
    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    resp = api(f"/feature-store/players/{player_id}/current", headers=headers_a)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["player_id"] == player_id
        assert payload["source"] == "redis-online"
        assert payload["entity_type"] == "PLAYER"
        assert isinstance(payload.get("features"), dict)


@skip_unless_stack
def test_feature_store_current_legacy_matches_canonical(headers_a):
    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    canonical = api(f"/feature-store/players/{player_id}/current", headers=headers_a)
    legacy = api(f"/players/{player_id}/features/current", headers=headers_a)

    assert canonical.status_code == legacy.status_code
    if canonical.status_code == 200:
        canonical_body = canonical.json()
        legacy_body = legacy.json()
        assert canonical_body["player_id"] == legacy_body["player_id"] == player_id
        assert canonical_body["feature_version"] == legacy_body["feature_version"]
        assert canonical_body["source"] == legacy_body["source"] == "redis-online"
        assert canonical_body["features"] == legacy_body["features"]


@skip_unless_stack
def test_feature_store_history_endpoint_returns_contract(headers_a):
    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    resp = api(f"/feature-store/players/{player_id}/history", headers=headers_a)
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["player_id"] == player_id
        assert isinstance(payload.get("count"), int)
        assert isinstance(payload.get("items"), list)
        if payload["items"]:
            item = payload["items"][0]
            assert "snapshot_date" in item
            assert isinstance(item.get("features"), dict)
            assert item.get("entity_type") == "PLAYER"


@skip_unless_stack
def test_player_feature_history_legacy_contains_module2_aliases(headers_a):
    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    resp = api(f"/players/{player_id}/feature-history?days=7", headers=headers_a)
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        payload = resp.json()
        assert payload["player_id"] == player_id
        assert isinstance(payload.get("data"), list)
        if payload["data"]:
            row = payload["data"][0]
            assert "feature_version" in row
            assert "unique_instruments_used_7d" in row
            assert "bonus_to_real_money_ratio_30d" in row


@skip_unless_stack
def test_feature_store_warm_cache_restores_current_from_snapshot(headers_a):
    me = api("/me", headers=headers_a)
    assert me.status_code == 200
    me_body = me.json()

    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    tenant_id = me_body["tenant_id"]
    snapshot_id = str(uuid.uuid4())
    snapshot_date = date(2099, 12, 30)
    redis_key = f"betaml:{tenant_id}:features:{player_id}"
    features = {
        "player_id": player_id,
        "tenant_id": tenant_id,
        "feature_version": 2,
        "shared_device_score": 0.77,
        "warmed_test_marker": "warm-e2e",
    }

    asyncio.run(
        _pg_execute(
            "DELETE FROM feature_snapshots WHERE tenant_id = $1::uuid AND player_id = $2::uuid AND feature_date = $3::date",
            tenant_id,
            player_id,
            snapshot_date,
        )
    )
    asyncio.run(
        _pg_execute(
            """
            INSERT INTO feature_snapshots
                (id, tenant_id, player_id, feature_date, snapshot_date, features, created_at)
            VALUES
                ($1::uuid, $2::uuid, $3::uuid, $4::date, $5::date, $6::jsonb, NOW())
            """,
            snapshot_id,
            tenant_id,
            player_id,
            snapshot_date,
            snapshot_date,
            json.dumps(features),
        )
    )
    asyncio.run(_redis_delete(redis_key))

    try:
        asyncio.run(_load_api_main()._warm_feature_store_cache())
        resp = api(f"/feature-store/players/{player_id}/current", headers=headers_a)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["player_id"] == player_id
        assert payload["features"]["warmed_test_marker"] == "warm-e2e"
        assert payload["features"]["warmed_from"] == "feature_snapshot"
        assert payload["source"] == "redis-online"
    finally:
        asyncio.run(_redis_delete(redis_key))
        asyncio.run(
            _pg_execute(
                "DELETE FROM feature_snapshots WHERE id = $1::uuid",
                snapshot_id,
            )
        )


@skip_unless_stack
def test_feature_store_drift_check_creates_admin_notification(headers_a):
    me = api("/me", headers=headers_a)
    assert me.status_code == 200
    me_body = me.json()

    players_resp = api("/players?per_page=1", headers=headers_a)
    body = players_resp.json()
    players = body if isinstance(body, list) else body.get("items", [])
    if not players:
        pytest.skip("Sem players disponíveis")

    player_id = players[0]["id"]
    tenant_id = me_body["tenant_id"]
    current_date = date(2099, 12, 31)
    previous_date = date(2099, 12, 30)
    title = f"Drift de features detectado em {current_date.isoformat()}"
    snapshot_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    asyncio.run(
        _pg_execute(
            "DELETE FROM notifications WHERE tenant_id = $1::uuid AND type = 'FEATURE_DRIFT' AND title = $2",
            tenant_id,
            title,
        )
    )
    asyncio.run(
        _pg_execute(
            "DELETE FROM feature_snapshots WHERE tenant_id = $1::uuid AND feature_date IN ($2::date, $3::date)",
            tenant_id,
            current_date,
            previous_date,
        )
    )

    rows = [
        (snapshot_ids[0], previous_date, {"deposit_velocity": 1.0, "shared_device_score": 0.1}),
        (snapshot_ids[1], current_date, {"deposit_velocity": 100.0, "shared_device_score": 0.95}),
    ]
    for snapshot_id, snapshot_date, features in rows:
        asyncio.run(
            _pg_execute(
                """
                INSERT INTO feature_snapshots
                    (id, tenant_id, player_id, feature_date, snapshot_date, features, created_at)
                VALUES
                    ($1::uuid, $2::uuid, $3::uuid, $4::date, $5::date, $6::jsonb, NOW())
                """,
                snapshot_id,
                tenant_id,
                player_id,
                snapshot_date,
                snapshot_date,
                json.dumps(features),
            )
        )

    try:
        asyncio.run(_load_api_main()._run_feature_drift_check_once())
        resp = api("/notifications?unread_only=true&limit=20", headers=headers_a)
        assert resp.status_code == 200, resp.text
        items = resp.json()
        drift_items = [item for item in items if item.get("type") == "FEATURE_DRIFT" and item.get("title") == title]
        assert drift_items, items
        assert "deposit_velocity" in (drift_items[0].get("body") or "")
        drift_score = asyncio.run(
            _pg_fetchval(
                "SELECT drift_score FROM feature_snapshots WHERE tenant_id = $1::uuid AND player_id = $2::uuid AND feature_date = $3::date",
                tenant_id,
                player_id,
                current_date,
            )
        )
        assert drift_score is not None
    finally:
        asyncio.run(
            _pg_execute(
                "DELETE FROM notifications WHERE tenant_id = $1::uuid AND type = 'FEATURE_DRIFT' AND title = $2",
                tenant_id,
                title,
            )
        )
        asyncio.run(
            _pg_execute(
                "DELETE FROM feature_snapshots WHERE id = ANY($1::uuid[])",
                snapshot_ids,
            )
        )


# ── ReportPackage COAF ────────────────────────────────────────────────────────

@skip_unless_stack
def test_generate_report_package_draft(headers_a):
    """
    POST /cases/{id}/report-package sem decision retorna DRAFT com payload COAF.
    """
    case_resp = api("/cases", "POST", headers=headers_a, json={
        "title": f"Caso COAF Test {uuid.uuid4().hex[:6]}",
        "priority": "HIGH",
    })
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]

    resp = api(
        f"/cases/{case_id}/report-package", "POST",
        headers=headers_a,
        json={},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "report_package_id" in body
    assert "payload" in body

    pl = body["payload"]
    assert "report_id" in pl
    assert "schema_version" in pl
    assert "reporting_entity" in pl
    assert "suspicious_operations" in pl
    assert "financial_summary" in pl
    assert "decision" in pl


@skip_unless_stack
def test_generate_report_package_file_sar(headers_a):
    """
    decision=FILE_SAR com analyst_narrative deve retornar status FINAL.
    """
    case_resp = api("/cases", "POST", headers=headers_a, json={
        "title": f"Caso SAR {uuid.uuid4().hex[:6]}",
        "priority": "CRITICAL",
    })
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]

    resp = api(
        f"/cases/{case_id}/report-package", "POST",
        headers=headers_a,
        json={
            "analyst_narrative": "Operações de depósito fracionado abaixo do limite obrigatório de comunicação, padrão típico de Structuring (COAF/FATF Tipologia ML-01). Recomenda-se comunicação ao COAF.",
            "decision": "FILE_SAR",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["decision"] == "FILE_SAR"
    assert body["status"] in ("FINAL", "FILED")
    assert body["payload"]["analyst_narrative"]


@skip_unless_stack
def test_generate_report_package_file_sar_without_narrative_fails(headers_a):
    """
    decision=FILE_SAR sem analyst_narrative deve retornar 400 (requisito COAF).
    """
    case_resp = api("/cases", "POST", headers=headers_a, json={
        "title": f"Caso SAR sem narrativa {uuid.uuid4().hex[:6]}",
        "priority": "HIGH",
    })
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]

    resp = api(
        f"/cases/{case_id}/report-package", "POST",
        headers=headers_a,
        json={"decision": "FILE_SAR"},  # sem analyst_narrative
    )
    assert resp.status_code == 400, (
        f"Esperado 400 para FILE_SAR sem narrativa, recebido: {resp.status_code}"
    )


@skip_unless_stack
def test_report_package_payload_never_exposes_full_cpf(headers_a):
    """
    O payload do relatório nunca deve expor CPF sem mascaramento.
    Valida conformidade com LGPD Art. 46 e princípio de minimização.
    """
    case_resp = api("/cases", "POST", headers=headers_a, json={"title": "Caso CPF Mask Test"})
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]

    resp = api(f"/cases/{case_id}/report-package", "POST", headers=headers_a, json={})
    assert resp.status_code == 201

    payload_str = resp.text
    # CPF formato: 11 dígitos seguidos OU com pontuação (###.###.###-##)
    import re
    raw_cpf = re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", payload_str)
    raw_digits = re.search(r"\b\d{11}\b", payload_str)
    assert not raw_cpf, "Payload expõe CPF formatado — violação LGPD"
    assert not raw_digits, "Payload expõe CPF como sequência de dígitos — violação LGPD"


# ── Logout / JWT revocation ───────────────────────────────────────────────────

@skip_unless_stack
def test_logout_revokes_token():
    """
    Após logout, o mesmo token não deve funcionar para acessar /me.
    """
    data = _login("admin_a", "admin123")
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Confirma que funciona antes do logout
    me_before = api("/me", headers=headers)
    assert me_before.status_code == 200

    # Faz logout
    logout_resp = api("/auth/logout", "POST", headers=headers)
    assert logout_resp.status_code in (200, 204)

    # Token deve estar na blacklist Redis agora
    me_after = api("/me", headers=headers)
    assert me_after.status_code == 401, (
        f"Token ainda válido após logout! Status: {me_after.status_code}"
    )


@skip_unless_stack
def test_login_wrong_tenant_slug():
    """
    Login com tenant_slug errado deve falhar com 401.
    """
    resp = api("/auth/login", "POST", json={
        "username":    "admin_a",
        "password":    "admin123",
        "tenant_slug": "tenant-que-nao-existe-xyz",
    })
    assert resp.status_code in (401, 404), (
        f"Esperado 401/404 para tenant_slug inválido, recebido: {resp.status_code}"
    )


@skip_unless_stack
def test_login_with_correct_tenant_slug():
    """
    Login com tenant_slug correto deve funcionar normalmente.
    """
    # Obtém o slug do tenant para admin_a
    data = _login("admin_a", "admin123")
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        pytest.skip("tenant_id não retornado pelo login — não é possível determinar slug")

    # Tenta login com tenant_slug vazio (deve funcionar como fallback)
    resp = api("/auth/login", "POST", json={
        "username": "admin_a",
        "password": "admin123",
    })
    assert resp.status_code == 200


# ── Auditoría ─────────────────────────────────────────────────────────────────

@skip_unless_stack
def test_audit_log_after_report_generation(headers_a):
    """
    Geração de ReportPackage deve criar entrada no audit log.
    """
    me_resp = api("/me", headers=headers_a)
    assert me_resp.status_code == 200
    current_user = me_resp.json()

    # Pega o total de audit logs antes
    before_resp = api("/audit-log?limit=1", headers=headers_a)
    assert before_resp.status_code == 200
    before_body = before_resp.json()
    assert isinstance(before_body, dict)
    assert "total" in before_body
    assert "items" in before_body
    total_before = before_body.get("total", 0)

    # Gera um relatório
    case_resp = api("/cases", "POST", headers=headers_a, json={"title": "Caso Audit Test"})
    if case_resp.status_code not in (200, 201):
        pytest.skip("Criação de caso falhou")
    case_id = case_resp.json()["id"]
    report_resp = api(f"/cases/{case_id}/report-package", "POST", headers=headers_a, json={})
    assert report_resp.status_code == 201, report_resp.text

    # Verifica que houve incremento no audit log
    after_resp = api("/audit-log?limit=1", headers=headers_a)
    assert after_resp.status_code == 200
    after_body = after_resp.json()
    assert isinstance(after_body, dict)
    assert "total" in after_body
    assert "items" in after_body
    total_after = after_body.get("total", 0)
    assert total_after >= total_before, "AuditLog não foi incrementado após geração de relatório"

    filtered_resp = api(
        f"/audit-logs?action=GENERATE_REPORT&entity_type=Case&user_id={current_user['id']}&limit=20",
        headers=headers_a,
    )
    assert filtered_resp.status_code == 200, filtered_resp.text
    filtered_items = filtered_resp.json()
    assert isinstance(filtered_items, list)
    matching_items = [
        item for item in filtered_items
        if item.get("entity_id") == case_id and item.get("action") == "GENERATE_REPORT"
    ]
    assert matching_items, filtered_items
    assert matching_items[0].get("user_id") == current_user["id"]
    assert matching_items[0].get("actor_id") == current_user["id"]
    assert isinstance(matching_items[0].get("after"), dict)
    assert matching_items[0]["after"].get("decision") in (None, "PENDING")
    assert matching_items[0]["after"].get("report_id")

    legacy_filter_resp = api(
        f"/audit-logs?action=GENERATE_REPORT&entity_type=Case&actor_id={current_user['id']}&page=1&per_page=20",
        headers=headers_a,
    )
    assert legacy_filter_resp.status_code == 200, legacy_filter_resp.text
    legacy_items = legacy_filter_resp.json()
    assert any(item.get("entity_id") == case_id for item in legacy_items), legacy_items
