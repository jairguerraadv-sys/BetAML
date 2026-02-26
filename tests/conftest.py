"""Shared pytest fixtures for BetAML test suite."""

import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from libs.dsl.parser import DSLEvaluator, DSLParser


@pytest.fixture
def sample_transaction_event():
    """Return a representative transaction event dict."""
    return {
        "transactionId": "txn-fixture-001",
        "tenantId": "tenant-001",
        "playerId": "player-fixture-001",
        "amount": "1500.00",
        "currency": "BRL",
        "type": "DEPOSIT",
        "occurredAt": "2024-06-01T12:00:00Z",
        "cpf": "52998224725",
        "sourceSystem": "BackofficeAlpha",
    }


@pytest.fixture
def sample_player_features():
    """Return a representative player feature dict for DSL evaluation."""
    return {
        "playerId": "player-fixture-001",
        "deposit_sum_24h": Decimal("1500.00"),
        "deposit_count_24h": 3,
        "withdrawal_sum_7d": Decimal("200.00"),
        "deposit_sum_7d": Decimal("3000.00"),
        "baseline_avg_daily_deposit": Decimal("400.00"),
        "baseline_stddev_deposit": Decimal("150.00"),
        "zscore_current_vs_baseline": Decimal("2.1"),
    }


@pytest.fixture
def dsl_evaluator():
    """Return a (parser, evaluator) tuple ready for use in tests."""
    return DSLParser(), DSLEvaluator()
