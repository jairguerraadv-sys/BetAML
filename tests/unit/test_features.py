"""
tests/unit/test_features.py
Unit tests for M2 feature computation logic in services/stream_processor/main.py.
These tests exercise the compute_features() helper in isolation.
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "stream_processor"))

# We need to mock heavy deps before importing
sys.modules.setdefault("aiokafka",      MagicMock())
sys.modules.setdefault("asyncpg",       MagicMock())
sys.modules.setdefault("redis.asyncio", MagicMock())
sys.modules.setdefault("clickhouse_driver", MagicMock())
sys.modules.setdefault("structlog",     MagicMock())
sys.modules.setdefault("minio",         MagicMock())

import importlib
sp = importlib.import_module("main")
compute_features = sp.compute_features_offline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_txn(
    amount: float = 100.0,
    txn_type: str = "DEPOSIT",
    currency: str = "BRL",
    instrument: str = "card_A",
    device_id: str = "dev_1",
    bank_id: str = "bank_X",
    odds: float = 2.0,
    hours_ago: int = 1,
    is_chargeback: bool = False,
    result: str = "WIN",
):
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {
        "amount":        amount,
        "txn_type":      txn_type,
        "currency":      currency,
        "instrument_id": instrument,
        "device_id":     device_id,
        "bank_id":       bank_id,
        "odds":          odds,
        "created_at":    ts.isoformat(),
        "is_chargeback": is_chargeback,
        "result":        result,
    }


def _history(txns):
    """Wrap txns list into the dict structure compute_features expects."""
    return {"transactions": txns}


# ── Deposit velocity ──────────────────────────────────────────────────────────

def test_deposit_velocity_basic():
    # 4 deposits in 24h window, span ~4 hours → velocity ~ 4/4 = 1.0 dep/h
    txns = [_make_txn(txn_type="DEPOSIT", hours_ago=i+1) for i in range(4)]
    feats = compute_features("P1", _history(txns))
    assert feats["deposit_velocity"] > 0


def test_deposit_velocity_zero_when_no_deposits():
    txns = [_make_txn(txn_type="BET", hours_ago=i+1) for i in range(5)]
    feats = compute_features("P1", _history(txns))
    assert feats["deposit_velocity"] == 0.0


# ── Unique instruments 7d ─────────────────────────────────────────────────────

def test_unique_instruments_7d():
    txns = [
        _make_txn(instrument="card_A", hours_ago=10),
        _make_txn(instrument="card_B", hours_ago=20),
        _make_txn(instrument="card_A", hours_ago=30),   # duplicate
        _make_txn(instrument="card_C", hours_ago=100),  # > 7d? No, 100h < 168h
    ]
    feats = compute_features("P1", _history(txns))
    assert feats["unique_instruments_7d"] == 3


def test_unique_instruments_7d_excludes_old():
    old = _make_txn(instrument="card_Z", hours_ago=200)  # > 7d
    recent = _make_txn(instrument="card_A", hours_ago=1)
    feats = compute_features("P1", _history([old, recent]))
    assert feats["unique_instruments_7d"] == 1


# ── Night activity ratio ──────────────────────────────────────────────────────

def test_night_activity_ratio_range():
    txns = [_make_txn(hours_ago=i) for i in range(10)]
    feats = compute_features("P1", _history(txns))
    assert 0.0 <= feats["night_activity_ratio"] <= 1.0


def test_weekend_activity_ratio_range():
    txns = [_make_txn(hours_ago=i * 12) for i in range(8)]
    feats = compute_features("P1", _history(txns))
    assert 0.0 <= feats["weekend_activity_ratio"] <= 1.0


# ── Inconsistent currency flag ───────────────────────────────────────────────

def test_inconsistent_currency_flag_true():
    txns = [
        _make_txn(currency="BRL"),
        _make_txn(currency="USD"),
    ]
    feats = compute_features("P1", _history(txns))
    assert feats["inconsistent_currency_flag"] is True


def test_inconsistent_currency_flag_false():
    txns = [_make_txn(currency="BRL") for _ in range(5)]
    feats = compute_features("P1", _history(txns))
    assert feats["inconsistent_currency_flag"] is False


# ── Win/loss ratio ────────────────────────────────────────────────────────────

def test_win_loss_ratio_all_wins():
    txns = [_make_txn(txn_type="BET", result="WIN", hours_ago=i*24) for i in range(5)]
    feats = compute_features("P1", _history(txns))
    # All wins → ratio = INF or very high; in practice capped or 1.0 when loss==0
    assert feats["win_loss_ratio_30d"] >= 0.0


def test_win_loss_ratio_mixed():
    wins  = [_make_txn(txn_type="BET", result="WIN",  hours_ago=i*24) for i in range(3)]
    losses= [_make_txn(txn_type="BET", result="LOSS", hours_ago=i*24+1) for i in range(3)]
    feats = compute_features("P1", _history(wins + losses))
    # 3 wins / 3 losses → ratio ≈ 1.0
    assert abs(feats["win_loss_ratio_30d"] - 1.0) < 0.01


def test_avg_odds_bet_7d():
    bets = [
        _make_txn(txn_type="BET", odds=1.5, hours_ago=2),
        _make_txn(txn_type="BET", odds=2.5, hours_ago=4),
    ]
    feats = compute_features("P1", _history(bets))
    assert feats["avg_odds_bet_7d"] == pytest.approx(2.0)


def test_avg_time_between_deposit_and_withdrawal_7d():
    deposit = _make_txn(txn_type="DEPOSIT", hours_ago=10)
    withdrawal = _make_txn(txn_type="WITHDRAWAL", hours_ago=8)
    feats = compute_features("P1", _history([deposit, withdrawal]))
    assert feats["avg_time_between_deposit_and_withdrawal_7d"] == pytest.approx(2.0)


# ── Chargeback rate ───────────────────────────────────────────────────────────

def test_chargeback_rate_zero():
    txns = [_make_txn(txn_type="DEPOSIT", is_chargeback=False, hours_ago=i*24) for i in range(5)]
    feats = compute_features("P1", _history(txns))
    assert feats["chargeback_rate_30d"] == 0.0


def test_chargeback_rate_nonzero():
    ok  = [_make_txn(txn_type="DEPOSIT", is_chargeback=False, hours_ago=i*24) for i in range(4)]
    bad = [_make_txn(txn_type="DEPOSIT", is_chargeback=True,  hours_ago=50)]
    feats = compute_features("P1", _history(ok + bad))
    assert feats["chargeback_rate_30d"] > 0.0


def test_bonus_to_real_money_ratio_30d():
    txns = [
        _make_txn(txn_type="DEPOSIT", hours_ago=2),
        _make_txn(txn_type="DEPOSIT", hours_ago=4),
        _make_txn(txn_type="BONUS", hours_ago=1),
    ]
    feats = compute_features("P1", _history(txns))
    assert feats["bonus_to_real_money_ratio_30d"] == pytest.approx(1 / 3)


def test_cashout_ratio_7d():
    bets = [
        {**_make_txn(txn_type="BET", hours_ago=2), "result": "CASHOUT", "cashout_amount": 90.0},
        _make_txn(txn_type="BET", hours_ago=4, result="LOSS"),
    ]
    feats = compute_features("P1", _history(bets))
    assert feats["cashout_ratio_7d"] == pytest.approx(0.5)


# ── Feature version ───────────────────────────────────────────────────────────

def test_feature_version_is_2():
    feats = compute_features("P1", _history([_make_txn()]))
    assert feats["feature_version"] == 2


def test_feature_aliases_present_for_module_2_contract():
    feats = compute_features("P1", _history([_make_txn(), _make_txn(instrument="card_B")]))
    assert "unique_instruments_used_7d" in feats
    assert feats["unique_instruments_used_7d"] == feats["unique_instruments_7d"]
    assert "bonus_to_real_money_ratio_30d" in feats
    assert "avg_time_between_deposit_and_withdrawal_7d" in feats


def test_cluster_metadata_present():
    txns = [
        _make_txn(device_id="dev_1", bank_id="bank_A"),
        _make_txn(device_id="dev_2", bank_id="bank_A", hours_ago=2),
    ]
    feats = compute_features("P1", _history(txns))
    assert feats["cluster_id"].startswith("cluster:")
    assert feats["cluster_size"] >= 1
    assert feats["shared_device_score"] >= 0.0
    assert feats["shared_instrument_score"] >= 0.0


# ── Empty history ─────────────────────────────────────────────────────────────

def test_empty_history_no_crash():
    feats = compute_features("P1", _history([]))
    assert feats["txn_count_24h"] == 0
    assert feats["feature_version"] == 2
