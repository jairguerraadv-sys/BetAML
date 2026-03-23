from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from unittest.mock import AsyncMock

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_RULES_MAIN = os.path.join(_ROOT, "services", "rules_engine", "main.py")

sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "rules_engine"))

_RULES_MODULE = None


def _load_module():
    global _RULES_MODULE
    if _RULES_MODULE is not None:
        return _RULES_MODULE
    spec = importlib.util.spec_from_file_location("rules_engine_main_runtime", _RULES_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError("Falha ao carregar services/rules_engine/main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rules_engine_main_runtime"] = module
    spec.loader.exec_module(module)
    _RULES_MODULE = module
    return module


def test_load_features_coerces_bool_int_and_float():
    rules = _load_module()
    redis = AsyncMock()
    redis.hgetall.return_value = {
        "feature_version": "2",
        "shared_device_score": "0.4",
        "multi_currency_flag": "true",
        "cluster_id": "cluster:abc",
    }

    features = asyncio.run(rules.load_features("tenant-1", "player-1", redis))

    assert features["feature_version"] == 2
    assert features["shared_device_score"] == 0.4
    assert features["multi_currency_flag"] is True
    assert features["cluster_id"] == "cluster:abc"


def test_evaluate_rules_includes_module2_feature_snapshot():
    rules = _load_module()
    envelope = {
        "entity_type": "TRANSACTION",
        "payload": {
            "player_id": "player-1",
            "amount": 1500,
            "type": "DEPOSIT",
            "status": "SETTLED",
            "currency": "BRL",
        },
    }
    features = {
        "deposit_sum_24h": 1500.0,
        "deposit_sum_7d": 4000.0,
        "deposit_count_24h": 4,
        "shared_device_count": 3,
        "shared_device_score": 0.6,
        "shared_instrument_score": 0.4,
        "deposit_velocity": 375.0,
        "night_activity_ratio": 0.8,
        "weekend_activity_ratio": 0.3,
        "chargeback_rate_30d": 0.1,
        "cashout_ratio_7d": 0.2,
        "unique_instruments_7d": 2,
        "bonus_to_real_money_ratio_30d": 0.5,
        "cluster_id": "cluster:xyz",
        "cluster_size": 4,
        "pep_flag": True,
        "declared_income_monthly": 9000,
    }
    ruleset = [{
        "id": 10,
        "name": "Device risk",
        "condition_dsl": "features.shared_device_count >= params.min_devices and player.pep_flag == true",
        "params": {"min_devices": 3},
        "severity": "HIGH",
        "scope": "TRANSACTION",
        "version": 2,
        "weight": 1.0,
    }]

    matches = asyncio.run(rules.evaluate_rules(envelope, features, ruleset))

    assert len(matches) == 1
    snapshot = matches[0]["features_snapshot"]
    assert snapshot["shared_device_score"] == 0.6
    assert snapshot["cluster_id"] == "cluster:xyz"
    assert snapshot["cluster_size"] == 4
    assert snapshot["bonus_to_real_money_ratio_30d"] == 0.5
    assert matches[0]["context_snapshot"]["player"]["pep_flag"] is True


def test_evaluate_rules_supports_compound_n_of_m():
    rules = _load_module()
    envelope = {
        "entity_type": "TRANSACTION",
        "payload": {"player_id": "player-1", "amount": 1500, "type": "DEPOSIT", "status": "SETTLED", "currency": "BRL"},
    }
    features = {"deposit_sum_24h": 1500.0}
    ruleset = [
        {"id": "r1", "name": "A", "condition_dsl": "transaction.amount > 1000", "params": {}, "severity": "HIGH", "scope": "TRANSACTION", "version": 1, "weight": 1.0},
        {"id": "r2", "name": "B", "condition_dsl": "transaction.type == 'DEPOSIT'", "params": {}, "severity": "MEDIUM", "scope": "TRANSACTION", "version": 1, "weight": 1.0},
        {"id": "r3", "name": "C", "condition_dsl": "transaction.amount > 999999", "params": {}, "severity": "LOW", "scope": "TRANSACTION", "version": 1, "weight": 1.0},
    ]
    compound_rules = [{
        "id": "c1",
        "name": "2 of 3",
        "operator": "N_OF_M",
        "n_threshold": 2,
        "component_rule_ids": ["r1", "r2", "r3"],
        "score_weights": {},
        "min_score_threshold": 0.5,
        "severity_mode": "MAX",
    }]

    matches = asyncio.run(rules.evaluate_rules(envelope, features, ruleset, compound_rules=compound_rules))

    compound = next((m for m in matches if m["rule"].get("is_compound")), None)
    assert compound is not None
    assert compound["rule"]["compound_rule_id"] == "c1"
    assert compound["context_snapshot"]["matched_components"] == 2
