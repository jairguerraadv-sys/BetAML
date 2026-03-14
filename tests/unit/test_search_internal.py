"""
tests/unit/test_search_internal.py — Unit tests for routers/search.py and routers/internal.py

Tests cover:
  - global_search: empty results shape, response structure with players/cases/alerts,
    LGPD write_audit call when players returned
  - alertmanager_webhook: 403 wrong secret, 400 invalid JSON, 200 no alerts,
    200 with alerts (correct processed count), critical severity log path
  - _ALERT_SEVERITY_MAP constant
  - Router registration (both routers)

Note: search.py builds SQLAlchemy Select expressions that include
Alert.id.cast("text").ilike() — this fails in the test environment because the
column type is not a proper TypeEngine. We patch select/or_/model-classes to
avoid expression-building errors while still exercising the response-building code.
"""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(tenant_id: str = "t1", role: str = "AML_ANALYST"):
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = tenant_id
    u.role = role
    return u


def _make_db_with_results(call_results: list | None = None):
    """
    Create an AsyncMock DB whose execute() returns successive items from call_results.
    Each item in call_results is the list returned by .scalars().all() for that call.
    Defaults to all-empty if call_results is None.
    """
    call_results = call_results or []
    call_count = [0]

    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=None)

    async def _execute(stmt):
        result = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        items = call_results[idx] if idx < len(call_results) else []
        result.scalars.return_value.all.return_value = items
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = _execute
    return db


def _chainable():
    """Return a MagicMock that supports arbitrary SQLAlchemy method chaining."""
    m = MagicMock()
    m.where.return_value = m
    m.order_by.return_value = m
    m.limit.return_value = m
    return m


def _make_request(secret: str = "test-secret", json_body=None, bad_json: bool = False):
    """Build a minimal FastAPI/Starlette Request mock for internal.py tests."""
    request = MagicMock()
    request.headers.get = MagicMock(return_value=secret)
    request.client.host = "127.0.0.1"
    if bad_json:
        request.json = AsyncMock(side_effect=Exception("Invalid JSON"))
    else:
        request.json = AsyncMock(return_value=json_body or {"status": "firing", "alerts": []})
    return request


# ---------------------------------------------------------------------------
# global_search — SQLAlchemy expression-build patch helper
# ---------------------------------------------------------------------------

def _sql_patches():
    """Return list of patch contexts that stub out the SQLAlchemy expression layer."""
    return [
        patch("routers.search.select", return_value=_chainable()),
        patch("routers.search.or_", return_value=MagicMock()),
        patch("routers.search.Player", MagicMock()),
        patch("routers.search.Case", MagicMock()),
        patch("routers.search.Alert", MagicMock()),
    ]


@pytest.mark.asyncio
async def test_global_search_empty_results():
    from routers.search import global_search
    db = _make_db_with_results()
    user = _make_user()

    with patch("routers.search.select", return_value=_chainable()), \
         patch("routers.search.or_", return_value=MagicMock()), \
         patch("routers.search.Player", MagicMock()), \
         patch("routers.search.Case", MagicMock()), \
         patch("routers.search.Alert", MagicMock()):
        result = await global_search(q="test", db=db, current_user=user)

    assert "players" in result
    assert "cases" in result
    assert "alerts" in result
    assert result["players"] == []
    assert result["cases"] == []
    assert result["alerts"] == []


@pytest.mark.asyncio
async def test_global_search_player_result_shape():
    """Player items have id, external_id, name, risk_band keys."""
    from routers.search import global_search

    player = MagicMock()
    player.id = "p-1"
    player.external_player_id = "ext-001"
    player.full_name = "João Silva"
    player.risk_band = "HIGH"

    # Call 1 = players query (returns player), calls 2+3 = cases/alerts (empty)
    db = _make_db_with_results([[player], [], []])
    user = _make_user()

    with patch("routers.search.select", return_value=_chainable()), \
         patch("routers.search.or_", return_value=MagicMock()), \
         patch("routers.search.Player", MagicMock()), \
         patch("routers.search.Case", MagicMock()), \
         patch("routers.search.Alert", MagicMock()), \
         patch("routers.search.write_audit", new_callable=AsyncMock):
        result = await global_search(q="João", db=db, current_user=user)

    assert len(result["players"]) == 1
    item = result["players"][0]
    assert item["id"] == "p-1"
    assert item["external_id"] == "ext-001"
    assert item["name"] == "João Silva"
    assert item["risk_band"] == "HIGH"


@pytest.mark.asyncio
async def test_global_search_pii_audit_called_when_players_found():
    """LGPD Art. 37: write_audit(pii_accessed='full_name') must be called when players returned."""
    from routers.search import global_search

    player = MagicMock()
    player.id = "p-1"
    player.external_player_id = "ext-001"
    player.full_name = "Test Player"
    player.risk_band = "LOW"

    db = _make_db_with_results([[player], [], []])
    user = _make_user()

    with patch("routers.search.select", return_value=_chainable()), \
         patch("routers.search.or_", return_value=MagicMock()), \
         patch("routers.search.Player", MagicMock()), \
         patch("routers.search.Case", MagicMock()), \
         patch("routers.search.Alert", MagicMock()), \
         patch("routers.search.write_audit", new_callable=AsyncMock) as mock_audit:
        await global_search(q="Test", db=db, current_user=user)

    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.call_args[1]
    assert call_kwargs.get("pii_accessed") == "full_name"


@pytest.mark.asyncio
async def test_global_search_case_result_shape():
    """Case items have id, reference_number, title, status keys."""
    from routers.search import global_search

    case = MagicMock()
    case.id = "c-1"
    case.reference_number = "REF-001"
    case.title = "Suspicious activity"
    case.status = "OPEN"

    # Call 1 = players (empty), call 2 = cases (with case), call 3 = alerts (empty)
    db = _make_db_with_results([[], [case], []])
    user = _make_user()

    with patch("routers.search.select", return_value=_chainable()), \
         patch("routers.search.or_", return_value=MagicMock()), \
         patch("routers.search.Player", MagicMock()), \
         patch("routers.search.Case", MagicMock()), \
         patch("routers.search.Alert", MagicMock()):
        result = await global_search(q="REF", db=db, current_user=user)

    assert len(result["cases"]) == 1
    item = result["cases"][0]
    assert item["id"] == "c-1"
    assert item["reference_number"] == "REF-001"
    assert item["title"] == "Suspicious activity"
    assert item["status"] == "OPEN"


@pytest.mark.asyncio
async def test_global_search_alert_result_shape():
    """Alert items have id, alert_type, severity, player_id keys."""
    from routers.search import global_search

    alert = MagicMock()
    alert.id = "a-1"
    alert.alert_type = "AML_HIGH_RISK"
    alert.severity = "HIGH"
    alert.player_id = "P-1"

    # Call 1 = players (empty), call 2 = cases (empty), call 3 = alerts (with alert)
    db = _make_db_with_results([[], [], [alert]])
    user = _make_user()

    with patch("routers.search.select", return_value=_chainable()), \
         patch("routers.search.or_", return_value=MagicMock()), \
         patch("routers.search.Player", MagicMock()), \
         patch("routers.search.Case", MagicMock()), \
         patch("routers.search.Alert", MagicMock()):
        result = await global_search(q="AML", db=db, current_user=user)

    assert len(result["alerts"]) == 1
    item = result["alerts"][0]
    assert item["id"] == "a-1"
    assert item["alert_type"] == "AML_HIGH_RISK"
    assert item["severity"] == "HIGH"


# ---------------------------------------------------------------------------
# alertmanager_webhook — 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_403_wrong_secret():
    from routers.internal import alertmanager_webhook
    from fastapi import HTTPException

    request = _make_request(secret="wrong-secret")

    with patch("routers.internal.settings") as mock_settings:
        mock_settings.internal_webhook_secret = "correct-secret"
        with pytest.raises(HTTPException) as exc:
            await alertmanager_webhook(request=request)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_webhook_400_invalid_json():
    from routers.internal import alertmanager_webhook

    request = _make_request(secret="test-secret", bad_json=True)

    with patch("routers.internal.settings") as mock_settings:
        mock_settings.internal_webhook_secret = "test-secret"
        response = await alertmanager_webhook(request=request)

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_webhook_200_no_alerts():
    from routers.internal import alertmanager_webhook

    request = _make_request(
        secret="test-secret",
        json_body={"status": "resolved", "alerts": []},
    )

    with patch("routers.internal.settings") as mock_settings:
        mock_settings.internal_webhook_secret = "test-secret"
        response = await alertmanager_webhook(request=request)

    import json
    body = json.loads(response.body)
    assert body["status"] == "ok"
    assert body["processed"] == 0


@pytest.mark.asyncio
async def test_webhook_200_with_alerts_returns_count():
    from routers.internal import alertmanager_webhook

    alerts = [
        {"labels": {"alertname": "HighLatency", "severity": "warning"},
         "annotations": {"summary": "Slow", "description": "API latency"}},
        {"labels": {"alertname": "KafkaLag", "severity": "critical"},
         "annotations": {"summary": "Behind", "description": "Consumer lag"}},
    ]
    request = _make_request(
        secret="test-secret",
        json_body={"status": "firing", "alerts": alerts},
    )

    with patch("routers.internal.settings") as mock_settings:
        mock_settings.internal_webhook_secret = "test-secret"
        response = await alertmanager_webhook(request=request)

    import json
    body = json.loads(response.body)
    assert body["status"] == "ok"
    assert body["processed"] == 2


@pytest.mark.asyncio
async def test_webhook_critical_severity_uses_critical_log():
    """Critical severity alerts must invoke logger.critical."""
    from routers.internal import alertmanager_webhook

    request = _make_request(
        secret="test-secret",
        json_body={
            "status": "firing",
            "alerts": [
                {"labels": {"alertname": "DBDown", "severity": "critical"},
                 "annotations": {"summary": "DB unreachable", "description": ""}},
            ],
        },
    )

    with patch("routers.internal.settings") as mock_settings, \
         patch("routers.internal.logger") as mock_logger:
        mock_settings.internal_webhook_secret = "test-secret"
        mock_logger.critical = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.info = MagicMock()
        await alertmanager_webhook(request=request)

    mock_logger.critical.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_warning_severity_uses_warning_log():
    from routers.internal import alertmanager_webhook

    request = _make_request(
        secret="test-secret",
        json_body={
            "status": "firing",
            "alerts": [
                {"labels": {"alertname": "SlowAPI", "severity": "warning"},
                 "annotations": {"summary": "Slow", "description": ""}},
            ],
        },
    )

    with patch("routers.internal.settings") as mock_settings, \
         patch("routers.internal.logger") as mock_logger:
        mock_settings.internal_webhook_secret = "test-secret"
        mock_logger.critical = MagicMock()
        mock_logger.warning = MagicMock()
        mock_logger.info = MagicMock()
        await alertmanager_webhook(request=request)

    mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _ALERT_SEVERITY_MAP constant
# ---------------------------------------------------------------------------

def test_alert_severity_map_has_expected_keys():
    from routers.internal import _ALERT_SEVERITY_MAP
    assert "critical" in _ALERT_SEVERITY_MAP
    assert "warning" in _ALERT_SEVERITY_MAP
    assert "info" in _ALERT_SEVERITY_MAP


def test_alert_severity_map_values():
    from routers.internal import _ALERT_SEVERITY_MAP
    assert _ALERT_SEVERITY_MAP["critical"] == "CRITICAL"
    assert _ALERT_SEVERITY_MAP["warning"] == "WARNING"
    assert _ALERT_SEVERITY_MAP["info"] == "INFO"


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def test_internal_router_has_webhook_path():
    from routers.internal import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/internal/alerts/webhook" in paths


def test_search_router_prefix():
    from routers.search import router
    assert router.prefix == "/search"


def test_search_router_has_get_endpoint():
    from routers.search import router
    methods = []
    for r in router.routes:
        if hasattr(r, "methods"):
            methods.extend(r.methods or [])
    assert "GET" in methods
