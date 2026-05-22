"""
tests/unit/test_rules.py — Unit tests for routers/rules.py

Tests cover:
  - RuleCreate / RuleUpdate / SimulateRequest / ValidateDSLRequest schemas
  - validate_rule_dsl: valid DSL passes, invalid DSL returns valid=False
  - list_rules: empty list, responds with rule dicts
  - create_rule: invalid DSL raises 400, valid rule persisted and returned
  - get_rule: 404, found with expected keys
  - update_rule: 404, status-only update (no version bump), DSL change increments version,
    invalid DSL raises 400
  - delete_rule: 404, sets status INACTIVE and returns message
  - simulate_rule: 404, evaluates events and returns matches count
"""
from __future__ import annotations

import sys
import os
from datetime import UTC, date, datetime
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


def _make_db(get_result=None):
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = _execute
    db.get = AsyncMock(return_value=get_result)
    return db


def _make_rule(tenant_id: str = "t1", rule_id: str = "r1", version: int = 1):
    r = MagicMock()
    r.id = rule_id
    r.tenant_id = tenant_id
    r.name = "Test Rule"
    r.description = "A test rule"
    r.status = "ACTIVE"
    r.severity = "MEDIUM"
    r.scope = "TRANSACTION"
    r.condition_dsl = "amount > 5000"
    r.params = {}
    r.version = version
    r.created_at = None
    r.updated_by = None
    return r


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_rule_create_defaults():
    from routers.rules import RuleCreate
    rc = RuleCreate(name="R1", condition_dsl="amount > 1000")
    assert rc.status == "ACTIVE"
    assert rc.severity == "MEDIUM"
    assert rc.scope == "TRANSACTION"
    assert rc.params == {}
    assert rc.weight == 0.5


def test_rule_update_all_optional():
    from routers.rules import RuleUpdate
    ru = RuleUpdate()
    assert ru.name is None
    assert ru.condition_dsl is None
    assert ru.params is None


def test_simulate_request_schema():
    from routers.rules import SimulateRequest
    req = SimulateRequest(events=[{"amount": 100}, {"amount": 50}])
    assert len(req.events) == 2


def test_validate_dsl_request_schema():
    from routers.rules import ValidateDSLRequest
    req = ValidateDSLRequest(expression="amount > 500")
    assert req.expression == "amount > 500"


# ---------------------------------------------------------------------------
# validate_rule_dsl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_rule_dsl_valid():
    from routers.rules import validate_rule_dsl, ValidateDSLRequest
    user = _make_user()
    body = ValidateDSLRequest(expression="amount > 500")

    db = _make_db()
    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        return res
    db.execute = _execute
    with patch("libs.dsl_parser.validate_dsl", return_value=(True, None)):
        result = await validate_rule_dsl(body=body, current_user=user, db=db)

    assert result["valid"] is True
    assert "válido" in result["message"]


@pytest.mark.asyncio
async def test_validate_rule_dsl_invalid():
    from routers.rules import validate_rule_dsl, ValidateDSLRequest
    user = _make_user()
    body = ValidateDSLRequest(expression="%%%bad dsl")

    db = _make_db()
    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        return res
    db.execute = _execute
    with patch("libs.dsl_parser.validate_dsl", return_value=(False, "syntax error at token %%%")):
        result = await validate_rule_dsl(body=body, current_user=user, db=db)

    assert result["valid"] is False
    assert "syntax error" in result["message"]


# ---------------------------------------------------------------------------
# list_rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_rules_empty():
    from routers.rules import list_rules
    db = _make_db()
    user = _make_user()

    result = await list_rules(current_user=user, db=db)

    assert result == []


@pytest.mark.asyncio
async def test_list_rules_returns_rule_dicts():
    from routers.rules import list_rules
    rule = _make_rule()
    db = _make_db()

    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = [rule]
        return res

    db.execute = _execute
    user = _make_user()

    result = await list_rules(current_user=user, db=db)

    assert len(result) == 1
    assert result[0]["id"] == "r1"
    assert result[0]["name"] == "Test Rule"
    assert result[0]["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# create_rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_rule_invalid_dsl_raises_400():
    from routers.rules import create_rule, RuleCreate
    from fastapi import HTTPException

    body = RuleCreate(name="Bad Rule", condition_dsl="%%%bad")
    db = _make_db()
    user = _make_user()

    with patch("libs.dsl_parser.validate_dsl", return_value=(False, "syntax error")):
        with pytest.raises(HTTPException) as exc:
            await create_rule(body=body, current_user=user, db=db)

    assert exc.value.status_code == 400
    assert "DSL" in exc.value.detail


@pytest.mark.asyncio
async def test_create_rule_valid_returns_id_and_status():
    from routers.rules import create_rule, RuleCreate

    body = RuleCreate(name="Good Rule", condition_dsl="amount > 500", weight=0.8)
    db = _make_db()
    user = _make_user()
    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        return res
    db.execute = _execute

    rule_mock = MagicMock()
    rule_mock.id = "new-rule-id"
    rule_mock.name = "Good Rule"
    rule_mock.status = "ACTIVE"

    with patch("libs.dsl_parser.validate_dsl", return_value=(True, None)), \
         patch("routers.rules.RuleDefinition", return_value=rule_mock), \
         patch("routers.rules.write_audit", new_callable=AsyncMock):
        result = await create_rule(body=body, current_user=user, db=db)

    assert result["id"] == "new-rule-id"
    assert result["status"] == "ACTIVE"


# ---------------------------------------------------------------------------
# get_rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_rule_404():
    from routers.rules import get_rule
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await get_rule(rule_id="nonexistent", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_rule_404_wrong_tenant():
    from routers.rules import get_rule
    from fastapi import HTTPException

    rule = _make_rule(tenant_id="other-tenant")
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    with pytest.raises(HTTPException) as exc:
        await get_rule(rule_id="r1", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_rule_found_has_expected_keys():
    from routers.rules import get_rule

    rule = _make_rule(tenant_id="t1")
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    result = await get_rule(rule_id="r1", current_user=user, db=db)

    for key in ("id", "name", "status", "severity", "scope", "condition_dsl", "version"):
        assert key in result


# ---------------------------------------------------------------------------
# update_rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_rule_404():
    from routers.rules import update_rule, RuleUpdate
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await update_rule(rule_id="x", body=RuleUpdate(), current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_rule_status_only_no_version_bump():
    from routers.rules import update_rule, RuleUpdate

    rule = _make_rule(tenant_id="t1", version=1)
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    with patch("routers.rules.write_audit", new_callable=AsyncMock):
        result = await update_rule(
            rule_id="r1",
            body=RuleUpdate(status="INACTIVE"),
            current_user=user,
            db=db,
        )

    assert rule.status == "INACTIVE"
    assert result["version"] == 1  # no DSL change → no version increment


@pytest.mark.asyncio
async def test_update_rule_dsl_change_increments_version():
    from routers.rules import update_rule, RuleUpdate

    rule = _make_rule(tenant_id="t1", version=2)
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")
    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        return res
    db.execute = _execute

    with patch("libs.dsl_parser.validate_dsl", return_value=(True, None)), \
         patch("routers.rules.write_audit", new_callable=AsyncMock):
        result = await update_rule(
            rule_id="r1",
            body=RuleUpdate(condition_dsl="amount > 9999"),
            current_user=user,
            db=db,
        )

    assert rule.version == 3
    assert result["version"] == 3


@pytest.mark.asyncio
async def test_update_rule_invalid_dsl_raises_400():
    from routers.rules import update_rule, RuleUpdate
    from fastapi import HTTPException

    rule = _make_rule(tenant_id="t1")
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")
    async def _execute(stmt):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        return res
    db.execute = _execute

    with patch("libs.dsl_parser.validate_dsl", return_value=(False, "parse error")):
        with pytest.raises(HTTPException) as exc:
            await update_rule(
                rule_id="r1",
                body=RuleUpdate(condition_dsl="%%%bad"),
                current_user=user,
                db=db,
            )

    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# delete_rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_rule_404():
    from routers.rules import delete_rule
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await delete_rule(rule_id="x", current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_rule_sets_inactive():
    from routers.rules import delete_rule

    rule = _make_rule(tenant_id="t1")
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    with patch("routers.rules.write_audit", new_callable=AsyncMock):
        result = await delete_rule(rule_id="r1", current_user=user, db=db)

    assert rule.status == "INACTIVE"
    assert "desativada" in result["message"]


# ---------------------------------------------------------------------------
# simulate_rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simulate_rule_404():
    from routers.rules import simulate_rule, SimulateRequest
    from fastapi import HTTPException

    db = _make_db(get_result=None)
    user = _make_user()

    with pytest.raises(HTTPException) as exc:
        await simulate_rule(rule_id="x", body=SimulateRequest(events=[]), current_user=user, db=db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_simulate_rule_evaluates_events():
    from routers.rules import simulate_rule, SimulateRequest

    rule = _make_rule(tenant_id="t1")
    rule.condition_dsl = "amount > 500"
    rule.params = {}
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    events = [{"amount": 1000}, {"amount": 100}, {"amount": 750}]

    with patch("libs.dsl_parser.eval_dsl", side_effect=[True, False, True]), \
         patch("routers.rules.write_audit", new_callable=AsyncMock) as write_audit_mock:
        result = await simulate_rule(
            rule_id="r1",
            body=SimulateRequest(events=events),
            current_user=user,
            db=db,
        )

    assert result["rule_id"] == "r1"
    assert result["matches"] == 2
    assert len(result["results"]) == 3
    write_audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_simulate_rule_historical_summary():
    from routers.rules import simulate_rule, SimulateRequest

    rule = _make_rule(tenant_id="t1")
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    alert1 = MagicMock()
    alert1.player_id = "p1"
    alert1.label = "TRUE_POSITIVE"
    alert1.created_at = datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC)
    alert2 = MagicMock()
    alert2.player_id = "p2"
    alert2.label = "FALSE_POSITIVE"
    alert2.created_at = datetime(2026, 3, 10, 13, 0, 0, tzinfo=UTC)

    calls = []

    async def _execute(stmt):
        res = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            res.scalars.return_value.all.return_value = []
        elif len(calls) == 2:
            res.scalars.return_value.all.return_value = [alert1, alert2]
        else:
            res.scalar.return_value = 2
        return res

    db.execute = _execute

    with patch("routers.rules.write_audit", new_callable=AsyncMock) as write_audit_mock:
        result = await simulate_rule(
            rule_id="r1",
            body=SimulateRequest(**{"from": date(2026, 3, 10), "to": date(2026, 3, 10)}),
            current_user=user,
            db=db,
        )

    assert result["matches"] == 2
    assert sorted(result["players"]) == ["p1", "p2"]
    assert result["false_positive_estimated"] == pytest.approx(0.5)
    assert result["precision_estimated"] == pytest.approx(0.5)
    assert result["timeline"][0]["alerts"] == 2
    write_audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_simulate_rule_handles_eval_exception():
    from routers.rules import simulate_rule, SimulateRequest

    rule = _make_rule(tenant_id="t1")
    rule.condition_dsl = "amount > 500"
    rule.params = {}
    db = _make_db(get_result=rule)
    user = _make_user(tenant_id="t1")

    with patch("libs.dsl_parser.eval_dsl", side_effect=ValueError("unknown field")):
        result = await simulate_rule(
            rule_id="r1",
            body=SimulateRequest(events=[{"x": 1}]),
            current_user=user,
            db=db,
        )

    assert result["matches"] == 0
    assert result["results"][0]["matched"] is False
    assert "error" in result["results"][0]


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

def test_rules_router_has_list_endpoint():
    from routers.rules import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/rules" in paths


def test_rules_router_has_validate_endpoint():
    from routers.rules import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/rules/validate" in paths


def test_rules_router_has_simulate_endpoint():
    from routers.rules import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/rules/{rule_id}/simulate" in paths


def test_rules_router_has_impact_trail_endpoint():
    from routers.rules import router
    paths = [r.path for r in router.routes if hasattr(r, "path")]
    assert "/rules/{rule_id}/impact-trail" in paths
