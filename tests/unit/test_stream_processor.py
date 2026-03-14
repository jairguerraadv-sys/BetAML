"""
Testes unitários do Stream Processor — BetAML.

Testa compute_features() diretamente, sem Kafka real.
Usa AsyncMock para simular o redis_client (Sorted Sets + Sets).

Cobre:
  - Player sem histórico → features são 0
  - Player com depósitos recentes → somas corretas
  - Player com saques e depósitos → ratio calculado
  - Baseline e z-score com dados históricos
  - Velocidade de depósitos (deposit_velocity)
  - Flag de actividade nocturna
  - Compartilhamento de dispositivo (shared_device_count)
  - Retorno de todas as chaves obrigatórias
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# ── Path setup ───────────────────────────────────────────────────────────────
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_ROOT, "libs"))
sys.path.insert(0, os.path.join(_ROOT, "services", "stream_processor"))

import main as sp  # noqa: E402

from main import compute_features  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Redis mock factory
# ─────────────────────────────────────────────────────────────────────────────

def _make_redis_mock(txn_entries: list[dict] | None = None, bet_entries: list[dict] | None = None,
                     player_device_ids: list[str] | None = None, device_member_count: int = 1,
                     player_bank_docs: list[str] | None = None, bank_member_count: int = 1):
    """Build an async Redis mock that returns the provided entries from zrange_by_score."""
    txn_entries = txn_entries or []
    bet_entries = bet_entries or []
    player_device_ids = player_device_ids or []
    player_bank_docs = player_bank_docs or []

    mock = MagicMock()

    # Key helpers (sync — just return strings)
    mock.txn_window_key.return_value = "txn_key"
    mock.bet_window_key.return_value = "bet_key"
    mock.player_devices_key.return_value = "device_set_key"
    mock.device_members_key.return_value = "device_members_key"
    mock.player_banks_key.return_value = "bank_set_key"
    mock.bank_members_key.return_value = "bank_members_key"

    def _zrange_by_score_side_effect(key, min_score):
        """Return JSON-encoded entries whose ts is after min_score."""
        if key == "txn_key":
            source = txn_entries
        elif key == "bet_key":
            source = bet_entries
        else:
            return AsyncMock(return_value=[])()

        filtered = []
        for entry in source:
            ts = entry["ts"]
            if isinstance(ts, str):
                ts_obj = datetime.fromisoformat(ts)
            else:
                ts_obj = ts
            if ts_obj.timestamp() >= min_score:
                serialized = {**entry, "ts": ts_obj.isoformat()}
                filtered.append(json.dumps(serialized, default=str))
        return filtered

    mock.zrange_by_score = AsyncMock(side_effect=_zrange_by_score_side_effect)
    mock.zadd_event = AsyncMock(return_value=None)

    # Set helpers (network/device correlation)
    mock.smembers_set = AsyncMock(side_effect=lambda key: (
        set(player_device_ids) if "device_set" in str(key) else set(player_bank_docs)
    ))
    mock.scard_set = AsyncMock(return_value=device_member_count)
    mock.hset_dict = AsyncMock(return_value=None)

    return mock


def _ch_mock():
    m = MagicMock()
    m.execute = AsyncMock(return_value=None)
    return m


REQUIRED_KEYS = {
    "player_id", "tenant_id",
    "deposit_sum_24h", "deposit_sum_7d", "deposit_sum_30d",
    "deposit_count_24h", "deposit_count_7d",
    "withdrawal_sum_24h", "withdrawal_sum_7d",
    "zscore_current_deposit_vs_baseline",
    "baseline_avg_daily_deposit", "baseline_stddev_deposit",
    "deposit_velocity",
    "night_activity_ratio", "weekend_activity_ratio",
    "shared_device_count",
    "shared_device_score",
    "shared_instrument_score",
    "cluster_id",
    "cluster_size",
    "chargeback_count_30d",
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ─────────────────────────────────────────────────────────────────────────────
# Test cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyHistory(unittest.TestCase):
    """Sem transações → valores 0 mas estrutura completa."""

    def setUp(self):
        self.redis = _make_redis_mock()
        self.ch = _ch_mock()

    def test_returns_all_required_keys(self):
        features = _run(compute_features("t1", "p1", self.redis, self.ch))
        missing = REQUIRED_KEYS - set(features.keys())
        self.assertFalse(missing, f"Chaves ausentes: {missing}")

    def test_numeric_features_are_zero(self):
        features = _run(compute_features("t1", "p1", self.redis, self.ch))
        self.assertEqual(features["deposit_sum_24h"], 0.0)
        self.assertEqual(features["withdrawal_sum_24h"], 0.0)
        self.assertEqual(features["deposit_count_24h"], 0)

    def test_player_and_tenant_ids_preserved(self):
        features = _run(compute_features("tenantX", "playerY", self.redis, self.ch))
        self.assertEqual(features["player_id"], "playerY")
        self.assertEqual(features["tenant_id"], "tenantX")


class TestDepositFeatures(unittest.TestCase):
    """Depósitos recentes → somas e contagens corretas."""

    def _make_txns(self, n: int, amount: float, hours_ago: float = 1.0, txn_type: str = "DEPOSIT") -> list[dict]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return [
            {"type": txn_type, "amount": amount, "status": "SETTLED",
             "ts": (now - timedelta(hours=hours_ago + i * 0.1)).isoformat(),
             "method": "PIX", "currency": "BRL"}
            for i in range(n)
        ]

    def test_deposit_sum_24h(self):
        txns = self._make_txns(3, 1000.0, hours_ago=0.5)
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertAlmostEqual(features["deposit_sum_24h"], 3000.0, places=1)

    def test_deposit_count_24h(self):
        txns = self._make_txns(5, 200.0, hours_ago=1.0)
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertEqual(features["deposit_count_24h"], 5)

    def test_deposit_older_than_24h_excluded_from_24h_window(self):
        old_txns = self._make_txns(3, 500.0, hours_ago=30.0)  # >24h ago
        recent_txns = self._make_txns(2, 100.0, hours_ago=1.0)
        redis = _make_redis_mock(txn_entries=old_txns + recent_txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertEqual(features["deposit_count_24h"], 2)
        self.assertAlmostEqual(features["deposit_sum_24h"], 200.0, places=1)

    def test_deposit_velocity_reflects_count(self):
        # 12 deposits in last hour → velocity = 12/24 = 0.5
        txns = self._make_txns(12, 100.0, hours_ago=0.5)
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertAlmostEqual(features["deposit_velocity"], 12 / 24.0, places=4)


class TestWithdrawalFeatures(unittest.TestCase):
    """Saques calculados corretamente."""

    def test_withdrawal_sum_24h(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        txns = [
            {"type": "WITHDRAWAL", "amount": 800.0, "status": "SETTLED",
             "ts": (now - timedelta(hours=2)).isoformat(), "method": "TED", "currency": "BRL"},
            {"type": "WITHDRAWAL", "amount": 600.0, "status": "SETTLED",
             "ts": (now - timedelta(hours=3)).isoformat(), "method": "TED", "currency": "BRL"},
        ]
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertAlmostEqual(features["withdrawal_sum_24h"], 1400.0, places=1)

    def test_ratio_withdraw_to_deposit_7d(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        txns = [
            {"type": "DEPOSIT", "amount": 10000.0, "status": "SETTLED",
             "ts": (now - timedelta(days=2)).isoformat(), "method": "PIX", "currency": "BRL"},
            {"type": "WITHDRAWAL", "amount": 9000.0, "status": "SETTLED",
             "ts": (now - timedelta(days=1)).isoformat(), "method": "TED", "currency": "BRL"},
        ]
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        ratio = features["ratio_withdrawal_to_deposit_7d"]
        self.assertAlmostEqual(ratio, 9000.0 / 10000.0, places=3)


class TestZscoreFeature(unittest.TestCase):
    """Z-score calculado a partir do baseline histórico."""

    def test_zscore_spike(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 15 dias de histórico com depósito ~1000/dia → baseline
        historical = [
            {"type": "DEPOSIT", "amount": 1000.0, "status": "SETTLED",
             "ts": (now - timedelta(days=d)).isoformat(), "method": "PIX", "currency": "BRL"}
            for d in range(2, 17)
        ]
        # Spike hoje: R$10 000
        spike = [
            {"type": "DEPOSIT", "amount": 10000.0, "status": "SETTLED",
             "ts": (now - timedelta(hours=1)).isoformat(), "method": "PIX", "currency": "BRL"}
        ]
        redis = _make_redis_mock(txn_entries=historical + spike)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertGreater(features["zscore_current_deposit_vs_baseline"], 1.0)

    def test_zscore_normal_is_low(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # Todos os depósitos iguais → std=0 → z=0
        txns = [
            {"type": "DEPOSIT", "amount": 1000.0, "status": "SETTLED",
             "ts": (now - timedelta(days=d)).isoformat(), "method": "PIX", "currency": "BRL"}
            for d in range(1, 20)
        ]
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        # std=0 → zscore formulado como 0 (proteção divisão por zero)
        self.assertEqual(features["zscore_current_deposit_vs_baseline"], 0.0)


class TestSharedDeviceFeature(unittest.TestCase):
    """shared_device_count reflete quantos outros players compartilham o device."""

    def test_no_devices_means_zero(self):
        redis = _make_redis_mock(player_device_ids=[], device_member_count=1)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertEqual(features["shared_device_count"], 0)

    def test_shared_by_4_counts_3(self):
        # scard retorna 4 (4 players usam o mesmo device), self = 1, shared = 3
        redis = _make_redis_mock(player_device_ids=["dev-001"], device_member_count=4)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertEqual(features["shared_device_count"], 3)


class TestNightActivityRatio(unittest.TestCase):
    """night_activity_ratio detecta movimentação noturna (22h–06h)."""

    def test_all_night_transactions_ratio_is_1(self):
        # Usa datas relativas a hoje para evitar que cutoff_7d exclua os eventos
        base_date = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
        txns = [
            {"type": "DEPOSIT", "amount": 100.0, "status": "SETTLED",
             "ts": (base_date - timedelta(days=d) + timedelta(hours=23)).isoformat(),
             "method": "PIX", "currency": "BRL"}
            for d in range(5)
        ]
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertAlmostEqual(features["night_activity_ratio"], 1.0, places=1)

    def test_no_night_transactions_ratio_is_0(self):
        base_date = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0)
        txns = [
            {"type": "DEPOSIT", "amount": 100.0, "status": "SETTLED",
             "ts": (base_date - timedelta(days=d) + timedelta(hours=14)).isoformat(),  # 14h = day
             "method": "PIX", "currency": "BRL"}
            for d in range(5)
        ]
        redis = _make_redis_mock(txn_entries=txns)
        features = _run(compute_features("t1", "p1", redis, _ch_mock()))
        self.assertEqual(features["night_activity_ratio"], 0.0)


class TestFeatureSnapshotPersistence(unittest.TestCase):
    def test_compute_features_persists_snapshot(self):
        redis = _make_redis_mock()
        calls = []

        async def _fake_to_thread(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", str(func)))
            return None

        with patch.object(sp.asyncio, "to_thread", side_effect=_fake_to_thread):
            _run(compute_features("t1", "p1", redis, _ch_mock()))

        assert "_ch_insert_features" in calls
        assert calls.count("_persist_feature_snapshot") == 1


if __name__ == "__main__":
    unittest.main()
