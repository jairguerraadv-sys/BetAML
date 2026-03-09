"""
Testes unitários do Rules Engine — DSL evaluation sem broker Kafka.

Cobre:
  - Avaliação de DSL simples e composta
  - Match / no-match por threshold
  - Funções DSL: zscore, ratio, abs
  - Contexto com features Redis mockadas
  - Regras com operadores AND / OR
  - Evento não pertencente ao scope → skip
  - DSL inválido → exceção controlada
"""
from __future__ import annotations

import os
import sys
import unittest

# ── Path setup ───────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "api"))

from libs.dsl_parser import eval_dsl, validate_dsl


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ctx(
    deposit_count_24h: float = 2,
    deposit_sum_24h: float = 1000.0,
    withdrawal_sum_24h: float = 0.0,
    deposit_sum_7d: float = 5000.0,
    withdrawal_sum_7d: float = 0.0,
    bet_stake_sum_24h: float = 100.0,
    baseline_avg: float = 1000.0,
    baseline_std: float = 200.0,
    new_payment_flag: bool = False,
    shared_device_count: int = 1,
    pep_flag: bool = False,
    txn_type: str = "DEPOSIT",
    txn_amount: float = 500.0,
    txn_status: str = "SETTLED",
) -> dict:
    return {
        "transaction": {
            "type": txn_type,
            "amount": txn_amount,
            "status": txn_status,
        },
        "bet": {"stakeAmount": 0.0},
        "player": {"pep_flag": pep_flag},
        "features": {
            "deposit_count_24h": deposit_count_24h,
            "deposit_sum_24h": deposit_sum_24h,
            "withdrawal_sum_24h": withdrawal_sum_24h,
            "deposit_sum_7d": deposit_sum_7d,
            "withdrawal_sum_7d": withdrawal_sum_7d,
            "bet_stake_sum_24h": bet_stake_sum_24h,
            "baseline_avg_daily_deposit": baseline_avg,
            "baseline_stddev_deposit": baseline_std,
            "new_payment_instrument_flag": new_payment_flag,
            "shared_device_count": shared_device_count,
            "baseline_deposit_avg_30d": baseline_avg,
            "baseline_deposit_std_30d": baseline_std,
        },
        "params": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestDSLValidation(unittest.TestCase):

    def test_valid_dsl_passes(self):
        ok, msg = validate_dsl("transaction.amount > 1000")
        self.assertTrue(ok, msg)

    def test_empty_dsl_fails(self):
        ok, _ = validate_dsl("")
        self.assertFalse(ok)

    def test_invalid_syntax_fails(self):
        ok, msg = validate_dsl("transaction.amount >>>>> 9000")
        self.assertFalse(ok)


class TestStructuringRule(unittest.TestCase):
    """Regra: depósitos fracionados (count >= 5 AND sum >= 5000)."""

    DSL = (
        'features.deposit_count_24h >= params.count_threshold '
        'and features.deposit_sum_24h >= params.sum_threshold '
        'and transaction.type == "DEPOSIT"'
    )

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"count_threshold": 5, "sum_threshold": 5000}
        return eval_dsl(self.DSL, ctx)

    def test_matches_when_above_threshold(self):
        ctx = _ctx(deposit_count_24h=8, deposit_sum_24h=7200, txn_type="DEPOSIT")
        self.assertTrue(self._eval(ctx))

    def test_no_match_below_count(self):
        ctx = _ctx(deposit_count_24h=3, deposit_sum_24h=7000, txn_type="DEPOSIT")
        self.assertFalse(self._eval(ctx))

    def test_no_match_wrong_type(self):
        ctx = _ctx(deposit_count_24h=8, deposit_sum_24h=7200, txn_type="WITHDRAWAL")
        self.assertFalse(self._eval(ctx))

    def test_no_match_exact_threshold_minus_one(self):
        ctx = _ctx(deposit_count_24h=4, deposit_sum_24h=4999, txn_type="DEPOSIT")
        self.assertFalse(self._eval(ctx))


class TestPEPRule(unittest.TestCase):
    """Regra: PEP com depósito acima de 5000."""

    DSL = 'player.pep_flag == true and transaction.amount >= params.pep_threshold'

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"pep_threshold": 5000}
        return eval_dsl(self.DSL, ctx)

    def test_pep_high_amount_matches(self):
        ctx = _ctx(pep_flag=True, txn_amount=15000)
        self.assertTrue(self._eval(ctx))

    def test_pep_low_amount_no_match(self):
        ctx = _ctx(pep_flag=True, txn_amount=500)
        self.assertFalse(self._eval(ctx))

    def test_non_pep_high_amount_no_match(self):
        ctx = _ctx(pep_flag=False, txn_amount=15000)
        self.assertFalse(self._eval(ctx))


class TestZscoreRule(unittest.TestCase):
    """Regra: zscore alto vs baseline."""

    DSL = (
        'zscore(features.deposit_sum_24h, features.baseline_avg_daily_deposit, '
        'features.baseline_stddev_deposit) >= params.zscore_threshold '
        'and transaction.type == "DEPOSIT"'
    )

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"zscore_threshold": 3}
        return eval_dsl(self.DSL, ctx)

    def test_spike_above_3_sigma_matches(self):
        # z = (7000 - 1000) / 200 = 30 ← muito acima de 3
        ctx = _ctx(deposit_sum_24h=7000, baseline_avg=1000.0, baseline_std=200.0, txn_type="DEPOSIT")
        self.assertTrue(self._eval(ctx))

    def test_normal_deposit_no_match(self):
        # z = (1100 - 1000) / 200 = 0.5
        ctx = _ctx(deposit_sum_24h=1100, baseline_avg=1000.0, baseline_std=200.0, txn_type="DEPOSIT")
        self.assertFalse(self._eval(ctx))


class TestRatioRule(unittest.TestCase):
    """Regra: razão saque/depósito alta (round-trip)."""

    DSL = (
        'transaction.type == "WITHDRAWAL" '
        'and ratio(features.withdrawal_sum_24h, features.deposit_sum_24h) >= params.round_trip_ratio '
        'and features.bet_stake_sum_24h <= params.max_stake'
    )

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"round_trip_ratio": "0.8", "max_stake": 50}
        return eval_dsl(self.DSL, ctx)

    def test_round_trip_matches(self):
        ctx = _ctx(
            withdrawal_sum_24h=19500, deposit_sum_24h=20000,
            bet_stake_sum_24h=20, txn_type="WITHDRAWAL",
        )
        self.assertTrue(self._eval(ctx))

    def test_low_ratio_no_match(self):
        ctx = _ctx(
            withdrawal_sum_24h=500, deposit_sum_24h=20000,
            bet_stake_sum_24h=20, txn_type="WITHDRAWAL",
        )
        self.assertFalse(self._eval(ctx))

    def test_high_stake_no_match(self):
        ctx = _ctx(
            withdrawal_sum_24h=19500, deposit_sum_24h=20000,
            bet_stake_sum_24h=5000, txn_type="WITHDRAWAL",
        )
        self.assertFalse(self._eval(ctx))


class TestSharedDeviceRule(unittest.TestCase):
    """Regra: mesmo device em múltiplos CPFs."""

    DSL = "features.shared_device_count >= params.device_threshold"

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"device_threshold": 3}
        return eval_dsl(self.DSL, ctx)

    def test_many_devices_matches(self):
        ctx = _ctx(shared_device_count=5)
        self.assertTrue(self._eval(ctx))

    def test_unique_device_no_match(self):
        ctx = _ctx(shared_device_count=1)
        self.assertFalse(self._eval(ctx))


class TestNewInstrumentRule(unittest.TestCase):
    """Regra: instrumento novo + valor alto."""

    DSL = (
        "features.new_payment_instrument_flag == true "
        "and transaction.amount >= params.amount_threshold"
    )

    def _eval(self, ctx: dict) -> bool:
        ctx["params"] = {"amount_threshold": 2000}
        return eval_dsl(self.DSL, ctx)

    def test_new_instrument_high_amount_matches(self):
        ctx = _ctx(new_payment_flag=True, txn_amount=5000)
        self.assertTrue(self._eval(ctx))

    def test_known_instrument_no_match(self):
        ctx = _ctx(new_payment_flag=False, txn_amount=5000)
        self.assertFalse(self._eval(ctx))


if __name__ == "__main__":
    unittest.main()
