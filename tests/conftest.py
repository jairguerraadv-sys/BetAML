"""
Pytest fixtures compartilhados.
"""
import sys
import os

# Adiciona libs/ ao path para todos os testes
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "libs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture
def transaction_ctx_normal():
    return {
        "transaction": {
            "amount": 500.0,
            "type": "DEPOSIT",
            "method": "PIX",
            "status": "COMPLETED",
            "currency": "BRL",
        },
        "features": {
            "deposit_sum_24h": 500.0,
            "deposit_sum_7d": 2000.0,
            "deposit_sum_30d": 8000.0,
            "deposit_count_24h": 1,
            "withdraw_sum_24h": 0.0,
            "baseline_deposit_avg_30d": 800.0,
            "baseline_deposit_std_30d": 200.0,
            "zscore_current_deposit_vs_baseline": 0.75,
            "new_payment_instrument_flag": False,
            "shared_device_count": 1,
            "pep_flag": False,
            "declared_income_monthly": 5000.0,
        },
        "bet": {},
        "player": {"pepFlag": False, "declaredIncomeMonthly": 5000.0},
        "params": {"threshold": 1000.0},
    }


@pytest.fixture
def transaction_ctx_suspicious():
    return {
        "transaction": {
            "amount": 9500.0,
            "type": "DEPOSIT",
            "method": "PIX",
            "status": "COMPLETED",
            "currency": "BRL",
        },
        "features": {
            "deposit_sum_24h": 9500.0,
            "deposit_sum_7d": 40000.0,
            "deposit_sum_30d": 100000.0,
            "deposit_count_24h": 5,
            "withdraw_sum_24h": 9000.0,
            "baseline_deposit_avg_30d": 2000.0,
            "baseline_deposit_std_30d": 500.0,
            "zscore_current_deposit_vs_baseline": 15.0,
            "new_payment_instrument_flag": True,
            "shared_device_count": 4,
            "pep_flag": True,
            "declared_income_monthly": 3000.0,
        },
        "bet": {"stakeAmount": 45000.0, "odds": 1.05},
        "player": {"pepFlag": True, "declaredIncomeMonthly": 3000.0},
        "params": {"threshold": 5000.0, "maxOdds": 1.10},
    }
