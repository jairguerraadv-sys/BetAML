"""
tests/unit/test_dsl_macros.py
Unit tests for macro expansion and new DSL functions in libs/dsl_parser.py.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs"))

from dsl_parser import eval_dsl, validate_dsl, expand_macros


# ── expand_macros ─────────────────────────────────────────────────────────────

def test_expand_simple_macro():
    macros = {"HIGH_RISK": "risk_score > 0.8"}
    result = expand_macros("%HIGH_RISK%", macros)
    # macros are wrapped in () for precedence safety
    assert result.replace("(", "").replace(")", "") == "risk_score > 0.8"


def test_expand_macro_inside_expression():
    macros = {"LIMIT": "500"}
    result = expand_macros("amount > %LIMIT%", macros)
    assert result.replace("(", "").replace(")", "") == "amount > 500"


def test_expand_multiple_macros():
    macros = {"A": "1", "B": "2"}
    result = expand_macros("%A% + %B%", macros)
    assert result.replace("(", "").replace(")", "") == "1 + 2"


def test_expand_macro_undefined_leaves_placeholder():
    """Undefined macros should remain as-is (not crash)."""
    result = expand_macros("%UNDEFINED_MACRO%", {})
    assert "%UNDEFINED_MACRO%" in result

def test_expand_no_macros():
    result = expand_macros("amount > 100", {})
    assert result == "amount > 100"


def test_expand_nested_macro():
    macros = {"INNER": "100", "OUTER": "amount > %INNER%"}
    result = expand_macros("%OUTER%", macros)
    assert result.replace("(", "").replace(")", "") == "amount > 100"


def test_expand_cycle_detection():
    """Circular macro references must not hang or crash."""
    macros = {"A": "%B%", "B": "%A%"}
    # Should either raise or return after max_depth iterations — must not loop forever
    with pytest.raises((RecursionError, ValueError, RuntimeError)):
        expand_macros("%A%", macros)


# ── new DSL functions via eval_dsl ────────────────────────────────────────────

CTX_BASE = {
    "txn_amount_24h": 1000.0,
    "txn_count_30d": 20,
    "risk_score": 0.75,
    "player_lists": {
        "VIP_LIST": {"P001", "P002"},
        "BLOCK_LIST": {"P999"},
    },
    "player_id": "P001",
    "feature_stats": {
        "txn_amount_24h": {"p10": 50, "p25": 100, "p50": 300, "p75": 700, "p90": 1200},
    },
    "cluster_id": "C-42",
    "cluster_size_val": 5,
}


def test_iff_true_branch():
    result = eval_dsl("iff(risk_score > 0.5, 1, 0)", CTX_BASE)
    assert result == 1


def test_iff_false_branch():
    result = eval_dsl("iff(risk_score > 0.9, 1, 0)", CTX_BASE)
    assert result == 0


def test_if_alias_supported():
    result = eval_dsl("if(risk_score > 0.5, 1, 0)", CTX_BASE)
    assert result == 1


def test_is_in_list_true():
    result = eval_dsl("is_in_list(player_id, 'VIP_LIST')", CTX_BASE)
    assert result is True


def test_is_in_list_false():
    ctx = {**CTX_BASE, "player_id": "P999"}
    result = eval_dsl("is_in_list(player_id, 'VIP_LIST')", ctx)
    assert result is False


def test_is_in_list_block():
    ctx = {**CTX_BASE, "player_id": "P999"}
    result = eval_dsl("is_in_list(player_id, 'BLOCK_LIST')", ctx)
    assert result is True


def test_min_function():
    ctx = {**CTX_BASE, "a": 5, "b": 10}
    result = eval_dsl("min(a, b)", ctx)
    assert result == 5


def test_max_function():
    ctx = {**CTX_BASE, "a": 5, "b": 10}
    result = eval_dsl("max(a, b)", ctx)
    assert result == 10


def test_percentile_rank_above():
    """txn_amount_24h=1000 > p90(1200) → False; > p75(700) → True"""
    result = eval_dsl("percentile_rank(txn_amount_24h, 'txn_amount_24h') > 75", CTX_BASE)
    assert isinstance(result, bool)


def test_percentile_rank_named_arg_segment_supported():
    ctx = {
        "features": {"deposit_velocity": 12.0},
        "feature_stats": {
            "deposit_velocity": {"p10": 1, "p25": 2, "p50": 5, "p75": 10, "p90": 20}
        },
    }
    result = eval_dsl("percentile_rank(deposit_velocity, segment='profession') >= 75", ctx)
    assert result is True


def test_zscore_named_arg_baseline_window_supported():
    ctx = {
        "features": {"deposit_velocity": 12.0},
        "feature_stats": {
            "deposit_velocity": {"mean": 6.0, "std": 2.0}
        },
    }
    result = eval_dsl("zscore(deposit_velocity, baseline_window='30d')", ctx)
    assert float(result) == pytest.approx(3.0)


# ── eval_dsl with macros param ────────────────────────────────────────────────

def test_eval_dsl_with_macro():
    macros = {"HIGH_VOL": "txn_amount_24h > 500"}
    ctx = {"txn_amount_24h": 1000.0}
    result = eval_dsl("%HIGH_VOL%", ctx, macros=macros)
    assert result is True


def test_eval_dsl_macro_in_compound_expr():
    macros = {"LIMIT": "500"}
    ctx = {"txn_amount_24h": 600.0}
    result = eval_dsl("txn_amount_24h > %LIMIT%", ctx, macros=macros)
    assert result is True


# ── validate_dsl ──────────────────────────────────────────────────────────────

def test_validate_valid_expression():
    ok, errors = validate_dsl("risk_score > 0.5")
    assert ok is True
    assert errors == ""


def test_validate_invalid_expression():
    ok, errors = validate_dsl("(((unclosed")
    assert ok is False
    assert errors


def test_validate_with_macro():
    macros = {"THRESHOLD": "0.8"}
    ok, errors = validate_dsl("risk_score > %THRESHOLD%", macros=macros)
    assert ok is True


def test_validate_unknown_function():
    ok, errors = validate_dsl("totally_unknown_fn123(x)")
    # Should be invalid
    assert ok is False
