"""Unit tests for libs/dsl/parser.py."""

import sys
import os
from decimal import Decimal

import pytest

# Ensure the repo root is on the path so libs can be imported directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from libs.dsl.parser import DSLParser, DSLEvaluator, DSLParseError, DSLEvalError


@pytest.fixture
def parser():
    return DSLParser()


@pytest.fixture
def evaluator():
    return DSLEvaluator()


def _eval(dsl: str, context: dict) -> bool:
    ast = DSLParser().parse(dsl)
    return DSLEvaluator().evaluate(ast, context)


# ---------------------------------------------------------------------------
# Basic comparison operators
# ---------------------------------------------------------------------------


class TestBasicComparisons:
    def test_greater_than_true(self):
        assert _eval("features.deposit_sum_24h > 100", {"features": {"deposit_sum_24h": 150}}) is True

    def test_greater_than_false(self):
        assert _eval("features.deposit_sum_24h > 100", {"features": {"deposit_sum_24h": 50}}) is False

    def test_less_than(self):
        assert _eval("features.deposit_sum_24h < 100", {"features": {"deposit_sum_24h": 50}}) is True

    def test_greater_than_or_equal_equal(self):
        assert _eval("transaction.amount >= 5000", {"transaction": {"amount": 5000}}) is True

    def test_greater_than_or_equal_greater(self):
        assert _eval("transaction.amount >= 5000", {"transaction": {"amount": 6000}}) is True

    def test_less_than_or_equal(self):
        assert _eval("transaction.amount <= 5000", {"transaction": {"amount": 4999}}) is True

    def test_equal(self):
        assert _eval("transaction.amount == 100", {"transaction": {"amount": 100}}) is True

    def test_not_equal(self):
        assert _eval("transaction.amount != 100", {"transaction": {"amount": 200}}) is True

    def test_equal_false(self):
        assert _eval("transaction.amount == 100", {"transaction": {"amount": 99}}) is False


# ---------------------------------------------------------------------------
# Logical operators: and / or / not
# ---------------------------------------------------------------------------


class TestLogicalOperators:
    def test_and_both_true(self):
        ctx = {"features": {"deposit_sum_24h": 200, "deposit_count_24h": 10}}
        assert _eval(
            "features.deposit_sum_24h > 100 and features.deposit_count_24h > 5", ctx
        ) is True

    def test_and_one_false(self):
        ctx = {"features": {"deposit_sum_24h": 50, "deposit_count_24h": 10}}
        assert _eval(
            "features.deposit_sum_24h > 100 and features.deposit_count_24h > 5", ctx
        ) is False

    def test_or_one_true(self):
        ctx = {"features": {"deposit_sum_24h": 200, "deposit_count_24h": 2}}
        assert _eval(
            "features.deposit_sum_24h > 100 or features.deposit_count_24h > 5", ctx
        ) is True

    def test_or_both_false(self):
        ctx = {"features": {"deposit_sum_24h": 50, "deposit_count_24h": 2}}
        assert _eval(
            "features.deposit_sum_24h > 100 or features.deposit_count_24h > 5", ctx
        ) is False

    def test_not_true(self):
        ctx = {"features": {"deposit_sum_24h": 50}}
        assert _eval("not features.deposit_sum_24h > 100", ctx) is True

    def test_not_false(self):
        ctx = {"features": {"deposit_sum_24h": 200}}
        assert _eval("not features.deposit_sum_24h > 100", ctx) is False

    def test_chained_and_or(self):
        ctx = {"a": {"x": 10}, "b": {"y": 20}, "c": {"z": 30}}
        assert _eval("a.x > 5 and b.y > 5 or c.z > 100", ctx) is True

    def test_double_not(self):
        ctx = {"features": {"deposit_sum_24h": 200}}
        assert _eval("not not features.deposit_sum_24h > 100", ctx) is True


# ---------------------------------------------------------------------------
# in operator
# ---------------------------------------------------------------------------


class TestInOperator:
    def test_in_true(self):
        ctx = {"transaction": {"type": "DEPOSIT"}}
        assert _eval("transaction.type in ['DEPOSIT', 'WITHDRAWAL']", ctx) is True

    def test_in_false(self):
        ctx = {"transaction": {"type": "BET"}}
        assert _eval("transaction.type in ['DEPOSIT', 'WITHDRAWAL']", ctx) is False

    def test_in_numeric(self):
        ctx = {"transaction": {"status_code": 2}}
        assert _eval("transaction.status_code in [1, 2, 3]", ctx) is True

    def test_in_single_item(self):
        ctx = {"player": {"country": "BR"}}
        assert _eval("player.country in ['BR']", ctx) is True


# ---------------------------------------------------------------------------
# zscore function
# ---------------------------------------------------------------------------


class TestZscoreFunction:
    def test_zscore_above_threshold(self):
        ctx = {
            "features": {
                "deposit_sum_24h": 1000,
                "baseline_avg": 100,
                "baseline_std": 50,
            }
        }
        assert _eval(
            "zscore(features.deposit_sum_24h, features.baseline_avg, features.baseline_std) > 2.5",
            ctx,
        ) is True

    def test_zscore_below_threshold(self):
        ctx = {
            "features": {
                "deposit_sum_24h": 120,
                "baseline_avg": 100,
                "baseline_std": 50,
            }
        }
        assert _eval(
            "zscore(features.deposit_sum_24h, features.baseline_avg, features.baseline_std) > 2.5",
            ctx,
        ) is False

    def test_zscore_exact(self):
        # (200 - 100) / 50 == 2.0
        ast = DSLParser().parse(
            "zscore(features.deposit_sum_24h, features.baseline_avg, features.baseline_std) == 2.0"
        )
        result = DSLEvaluator().evaluate(
            ast,
            {"features": {"deposit_sum_24h": 200, "baseline_avg": 100, "baseline_std": 50}},
        )
        assert result is True

    def test_zscore_zero_stddev_raises(self):
        ctx = {"features": {"v": 10, "m": 5, "s": 0}}
        with pytest.raises(DSLEvalError, match="stddev must not be zero"):
            _eval("zscore(features.v, features.m, features.s) > 1", ctx)


# ---------------------------------------------------------------------------
# ratio function
# ---------------------------------------------------------------------------


class TestRatioFunction:
    def test_ratio_above_threshold(self):
        ctx = {"features": {"withdrawal_sum_7d": 900, "deposit_sum_7d": 1000}}
        assert _eval(
            "ratio(features.withdrawal_sum_7d, features.deposit_sum_7d) > 0.8", ctx
        ) is True

    def test_ratio_below_threshold(self):
        ctx = {"features": {"withdrawal_sum_7d": 500, "deposit_sum_7d": 1000}}
        assert _eval(
            "ratio(features.withdrawal_sum_7d, features.deposit_sum_7d) > 0.8", ctx
        ) is False

    def test_ratio_zero_denominator_raises(self):
        ctx = {"features": {"a": 100, "b": 0}}
        with pytest.raises(DSLEvalError, match="denominator must not be zero"):
            _eval("ratio(features.a, features.b) > 1", ctx)

    def test_ratio_exact(self):
        ctx = {"features": {"a": 1, "b": 2}}
        # 1/2 == 0.5
        assert _eval("ratio(features.a, features.b) == 0.5", ctx) is True


# ---------------------------------------------------------------------------
# DSLParseError on invalid syntax
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_empty_string_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("")

    def test_whitespace_only_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("   ")

    def test_unexpected_character_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("features.x @ 10")

    def test_incomplete_expression_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("features.x >")

    def test_unterminated_list_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("features.x in [1, 2")

    def test_trailing_token_raises(self, parser):
        with pytest.raises(DSLParseError):
            parser.parse("features.x > 10 features.y")


# ---------------------------------------------------------------------------
# Field access patterns
# ---------------------------------------------------------------------------


class TestFieldAccess:
    def test_transaction_amount(self):
        assert _eval("transaction.amount > 100", {"transaction": {"amount": 200}}) is True

    def test_player_pep_flag_true(self):
        assert _eval("player.pepFlag == true", {"player": {"pepFlag": True}}) is True

    def test_player_pep_flag_false(self):
        assert _eval("player.pepFlag == false", {"player": {"pepFlag": False}}) is True

    def test_features_generic(self):
        assert _eval("features.score > 0.9", {"features": {"score": 0.95}}) is True

    def test_missing_field_raises(self):
        with pytest.raises(DSLEvalError):
            _eval("features.nonexistent > 100", {"features": {}})

    def test_missing_namespace_raises(self):
        with pytest.raises(DSLEvalError):
            _eval("features.x > 100", {})

    def test_deep_field_access(self):
        ctx = {"a": {"b": {"c": 42}}}
        assert _eval("a.b.c == 42", ctx) is True
