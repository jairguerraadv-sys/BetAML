from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../services/api"))


def _user():
    u = MagicMock()
    u.id = "u1"
    u.tenant_id = "t1"
    return u


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_update_compound_rule_updates_logic_and_version():
    from routers.compound_rules import CompoundRuleUpdate, update_compound_rule

    row = MagicMock()
    row.id = "c1"
    row.tenant_id = "t1"
    row.name = "Original"
    row.logic = "AND"
    row.operator = "AND"
    row.component_rule_ids = ["r1", "r2"]
    row.child_rule_ids = ["r1", "r2"]
    row.min_score_threshold = 0.5
    row.version = 1
    row.score_weights = {}

    db = _db()
    calls = []

    async def execute(stmt):
        res = MagicMock()
        calls.append(len(calls))
        if len(calls) == 1:
            res.scalar_one_or_none.return_value = row
        else:
            res.scalars.return_value.all.return_value = ["r1", "r2"]
        return res

    db.execute = execute

    with patch("routers.compound_rules.write_audit", AsyncMock()):
        result = await update_compound_rule(
            "c1",
            CompoundRuleUpdate(logic="OR", component_rule_ids=["r1", "r2"]),
            db=db,
            current_user=_user(),
        )

    assert row.logic == "OR"
    assert row.operator == "OR"
    assert row.version == 2
    assert result is row
