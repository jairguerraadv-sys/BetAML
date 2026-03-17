"""
tests/unit/test_dsl_arithmetic.py — Unit tests for arithmetic operators (+, -, /)
added to libs/dsl_parser.py in Module 3.

Tests cover:
  - Addition (+): two fields, field + literal, combined with comparisons
  - Subtraction (-): field - field, result below zero
  - Division (/): field / field, division by zero safe (returns 0), result check
  - Operator precedence: * before +/-,  / before +/-,  parentheses override
  - Combined with logical operators and boolean context
  - validate_dsl accepts arithmetic expressions
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from libs.dsl_parser import eval_dsl, validate_dsl, parse_dsl, DSLSyntaxError


# ── Helpers ──────────────────────────────────────────────────────────────────

def ctx(**features) -> dict:
    return {
        "features": {k: Decimal(str(v)) if not isinstance(v, bool) else v for k, v in features.items()},
        "transaction": {},
        "player": {},
        "params": {},
    }


# ── Addition ─────────────────────────────────────────────────────────────────

def test_add_two_features():
    c = ctx(deposit_sum_24h=300, withdrawal_sum_24h=200)
    assert eval_dsl("features.deposit_sum_24h + features.withdrawal_sum_24h > 400", c) is True


def test_add_feature_and_literal():
    c = ctx(deposit_count_24h=5)
    assert eval_dsl("features.deposit_count_24h + 2 > 6", c) is True


def test_add_equals():
    c = ctx(a=3, b=7)
    assert eval_dsl("features.a + features.b == 10", c) is True


def test_add_does_not_match_when_below():
    c = ctx(deposit_sum_24h=100, withdrawal_sum_24h=150)
    assert eval_dsl("features.deposit_sum_24h + features.withdrawal_sum_24h > 500", c) is False


# ── Subtraction ───────────────────────────────────────────────────────────────

def test_subtract_features():
    c = ctx(deposit_sum_30d=10000, chargeback_count_30d=3)
    assert eval_dsl("features.deposit_sum_30d - features.chargeback_count_30d > 9990", c) is True


def test_subtract_result_below_zero():
    """5 - 10 = -5, which is < 0."""
    c = ctx(a=5, b=10)
    assert eval_dsl("features.a - features.b < 0", c) is True


def test_subtract_equals_zero():
    c = ctx(x=42, y=42)
    assert eval_dsl("features.x - features.y == 0", c) is True


# ── Division ──────────────────────────────────────────────────────────────────

def test_divide_features():
    c = ctx(deposit_sum_30d=10000, deposit_count_24h=4)
    # 10000 / 4 = 2500
    assert eval_dsl("features.deposit_sum_30d / features.deposit_count_24h > 2000", c) is True


def test_division_result_exact():
    c = ctx(a=100, b=4)
    assert eval_dsl("features.a / features.b == 25", c) is True


def test_division_by_zero_returns_zero_not_error():
    """Division by zero must not raise — DSL returns 0, so comparison fails silently."""
    c = ctx(a=1000, b=0)
    # 1000 / 0 → 0, so 0 >= 10000 is False
    assert eval_dsl("features.a / features.b >= 10000", c) is False


def test_division_by_zero_eq_zero():
    """Explicitly checking that a / 0 evaluates to 0."""
    c = ctx(a=500, b=0)
    assert eval_dsl("features.a / features.b == 0", c) is True


# ── Operator precedence ───────────────────────────────────────────────────────

def test_mul_binds_tighter_than_add():
    """a + b * c  should compute b*c first: 10 + 5*3 = 25, NOT (10+5)*3 = 45."""
    c = ctx(a=10, b=5, c=3)
    assert eval_dsl("features.a + features.b * features.c == 25", c) is True


def test_mul_binds_tighter_than_sub():
    """a - b * c: 20 - 2*4 = 12."""
    c = ctx(a=20, b=2, c=4)
    assert eval_dsl("features.a - features.b * features.c == 12", c) is True


def test_div_binds_tighter_than_add():
    """a + b / c: 10 + 20/4 = 15."""
    c = ctx(a=10, b=20, c=4)
    assert eval_dsl("features.a + features.b / features.c == 15", c) is True


def test_parentheses_override_precedence():
    """(a + b) * c: (10+5)*3 = 45."""
    c = ctx(a=10, b=5, c=3)
    assert eval_dsl("(features.a + features.b) * features.c == 45", c) is True


def test_left_to_right_add_sub():
    """a - b + c: left-associative, (10-3)+2 = 9."""
    c = ctx(a=10, b=3, c=2)
    assert eval_dsl("features.a - features.b + features.c == 9", c) is True


# ── Combined with logical operators ────────────────────────────────────────────

def test_arithmetic_in_and_expression():
    c = ctx(deposit_sum_30d=50000, chargeback_count_30d=3)
    dsl = (
        "features.deposit_sum_30d / 1000 > 40 and "
        "features.chargeback_count_30d + 1 >= 4"
    )
    assert eval_dsl(dsl, c) is True


def test_arithmetic_in_or_expression():
    c = ctx(a=5, b=100)
    assert eval_dsl("features.a + 100 > 200 or features.b - 10 > 80", c) is True


# ── validate_dsl accepts arithmetic ───────────────────────────────────────────

def test_validate_addition_expression():
    ok, msg = validate_dsl("features.a + features.b > 10")
    assert ok is True
    assert msg == ""


def test_validate_division_expression():
    ok, msg = validate_dsl("features.deposit_sum_30d / features.deposit_count_24h >= 1000")
    assert ok is True
    assert msg == ""


def test_validate_mixed_arithmetic():
    ok, msg = validate_dsl("(features.a + features.b) * 2 - features.c / 4 > 100")
    assert ok is True
    assert msg == ""


def test_parse_complex_arithmetic():
    ast = parse_dsl("features.a + features.b * 2 - features.c / 4")
    assert ast is not None


# ── Real-world AML DSL examples with arithmetic ────────────────────────────────

def test_total_volume_arithmetic():
    """Structuring check: deposits + withdrawals > threshold."""
    c = {
        "features": {
            "deposit_sum_30d": Decimal("45000"),
            "withdrawal_sum_7d": Decimal("12000"),
        },
        "transaction": {},
        "player": {},
        "params": {"threshold": Decimal("50000")},
    }
    # 45000 + 12000 = 57000 > 50000
    assert eval_dsl(
        "features.deposit_sum_30d + features.withdrawal_sum_7d > params.threshold", c
    ) is True


def test_net_flow_subtraction():
    """Net inflow check: deposits - withdrawals > limit."""
    c = {
        "features": {
            "deposit_sum_30d": Decimal("80000"),
            "withdrawal_sum_7d": Decimal("70000"),
        },
        "transaction": {}, "player": {}, "params": {},
    }
    # 80000 - 70000 = 10000 > 5000
    assert eval_dsl(
        "features.deposit_sum_30d - features.withdrawal_sum_7d > 5000", c
    ) is True
