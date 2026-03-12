from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PLAYERS_ROUTER = os.path.join(_ROOT, "services", "api", "routers", "players.py")

sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "api"))

_PLAYERS_MODULE = None


def _load_module():
    global _PLAYERS_MODULE
    if _PLAYERS_MODULE is not None:
        return _PLAYERS_MODULE
    spec = importlib.util.spec_from_file_location("api_players_router_test", _PLAYERS_ROUTER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Falha ao carregar services/api/routers/players.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["api_players_router_test"] = module
    spec.loader.exec_module(module)
    _PLAYERS_MODULE = module
    return module


def test_normalize_feature_history_row_adds_legacy_aliases():
    players = _load_module()
    columns = [
        "feature_date",
        "unique_instruments_7d",
        "bonus_to_real_ratio_30d",
        "shared_instrument_score",
        "feature_version",
    ]
    row = ("2026-03-10", 3, 0.25, 0.4, 2)

    record = players._normalize_feature_history_row(columns, row)

    assert record["unique_instruments_7d"] == 3.0
    assert record["unique_instruments_used_7d"] == 3.0
    assert record["bonus_to_real_ratio_30d"] == 0.25
    assert record["bonus_to_real_money_ratio_30d"] == 0.25
    assert record["shared_instrument_score"] == 0.4
    assert record["feature_version"] == 2.0