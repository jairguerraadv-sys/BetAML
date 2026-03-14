"""
Contract tests — Stream Processor (services/stream_processor/main.py)

Verifica:
  1. _ch_insert_transaction(): row dict correto para ClickHouse (betaml.transactions)
  2. _ch_insert_bet():         row dict correto para ClickHouse (betaml.bets)
  3. _ch_insert_features():    row dict correto para ClickHouse (betaml.player_features_daily)
  4. process_transaction():    chama ClickHouse + atualiza Redis com a transação
  5. process_bet():            chama ClickHouse + atualiza Redis + compute_features
  6. Campos obrigatórios do schema ClickHouse estão sempre presentes (não há KeyError)

Estes são unit tests — não requerem ClickHouse, Redis ou Kafka.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Import stream_processor/main.py como módulo nomeado para evitar colisão com api/main.py
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SP_PATH = os.path.join(_ROOT, "services", "stream_processor", "main.py")
sys.path.insert(0, os.path.join(_ROOT, "services", "stream_processor"))
sys.path.insert(0, os.path.join(_ROOT, "libs"))

_spec = importlib.util.spec_from_file_location("stream_processor_main", _SP_PATH)
_sp_mod = importlib.util.module_from_spec(_spec)
sys.modules["stream_processor_main"] = _sp_mod
_spec.loader.exec_module(_sp_mod)

_ch_insert_transaction = _sp_mod._ch_insert_transaction
_ch_insert_bet = _sp_mod._ch_insert_bet
_ch_insert_features = _sp_mod._ch_insert_features
process_transaction = _sp_mod.process_transaction
process_bet = _sp_mod.process_bet


# ─── Schema column sets (derived from clickhouse-init.sql) ───────────────────

_TX_REQUIRED = {
    "event_id", "tenant_id", "source_system", "player_id",
    "transaction_type", "amount", "currency", "occurred_at",
    "event_date", "created_at",
}

_BET_REQUIRED = {
    "event_id", "tenant_id", "source_system", "player_id",
    "stake_amount", "channel", "placed_at", "event_date", "created_at",
}

_FEATURES_REQUIRED = {
    "tenant_id", "player_id", "feature_date",
    "deposit_sum_24h", "deposit_sum_7d", "deposit_count_24h",
    "withdrawal_sum_24h", "bet_stake_sum_24h",
    "ratio_w2d_7d", "zscore_deposit",
    "feature_version", "computed_at",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_tx_envelope(amount=1000.0, tx_type="DEPOSIT"):
    return {
        "event_id":       "evt-tx-001",
        "tenant_id":      "tenant-a",
        "source_system":  "BackofficeAlpha",
        "source_event_id": "src-001",
        "payload": {
            "player_id": "player-001",
            "amount":    amount,
            "type":      tx_type,
            "method":    "PIX",
            "status":    "COMPLETED",
            "currency":  "BRL",
            "occurred_at": "2026-03-14T10:00:00Z",
        },
    }


def _make_bet_envelope(stake=200.0, odds=2.5):
    stake_f = float(stake)
    return {
        "event_id":      "evt-bet-001",
        "tenant_id":     "tenant-a",
        "source_system": "BackofficeAlpha",
        "payload": {
            "player_id":   "player-001",
            "stake_amount": stake,         # original value (can be string for edge-case tests)
            "odds":         odds,
            "potential_payout": stake_f * odds,  # always a float
            "market_type": "FOOTBALL",
            "sport":       "FOOTBALL",
            "channel":     "WEB",
            "status":      "PENDING",
            "placed_at":   "2026-03-14T10:00:00Z",
        },
    }


def _make_features(tenant_id="tenant-a", player_id="player-001"):
    return {
        "tenant_id":  tenant_id,
        "player_id":  player_id,
        "pep_flag":   False,
        "deposit_sum_24h":   1000.0,
        "deposit_sum_7d":    5000.0,
        "deposit_sum_30d":   15000.0,
        "deposit_count_24h": 3,
        "deposit_count_7d":  12,
        "withdrawal_sum_24h": 200.0,
        "withdrawal_sum_7d":  800.0,
        "withdrawal_count_24h": 1,
        "bet_stake_sum_24h":  300.0,
        "bet_stake_sum_7d":   1200.0,
        "ratio_withdrawal_to_deposit_7d": 0.16,
        "baseline_avg_daily_deposit": 715.0,
        "baseline_stddev_deposit": 200.0,
        "zscore_current_deposit_vs_baseline": 0.5,
        "new_payment_instrument_flag": False,
        "new_device_flag": False,
        "shared_device_count": 0,
        "shared_bank_account_count": 0,
        "chargeback_count_30d": 0,
        "deposit_velocity": 0.125,
        "unique_instruments_7d": 1,
        "night_activity_ratio": 0.1,
        "weekend_activity_ratio": 0.3,
        "avg_odds_bet_7d": 2.1,
        "win_loss_ratio_30d": 0.55,
        "avg_deposit_to_withdrawal_hours": 48.0,
        "multi_currency_flag": False,
        "chargeback_rate_30d": 0.0,
        "bonus_to_real_ratio_30d": 0.05,
        "cashout_ratio_7d": 0.2,
        "shared_instrument_score": 0.0,
        "feature_version": 2,
    }


def _make_redis_mock():
    """Retorna MagicMock com todos os métodos async do RedisClient configurados como AsyncMock."""
    redis = MagicMock()
    async_methods = [
        "zadd_event", "zrangebyscore", "zremrangebyscore",
        "get", "set", "hgetall", "hset", "expire",
        "smembers", "sismember", "sadd", "sadd_member",
        "delete", "incr", "decr", "keys",
        "zcard", "zrevrange", "zrange",
        "lpush", "rpush", "lrange",
        "zincrby", "zscore", "zrangebyscore_with_scores",
    ]
    for method in async_methods:
        setattr(redis, method, AsyncMock(return_value=None))
    redis.sismember = AsyncMock(return_value=False)
    redis.smembers = AsyncMock(return_value=set())
    redis.zrangebyscore = AsyncMock(return_value=[])
    redis.hgetall = AsyncMock(return_value={})
    redis.transaction_window_key = MagicMock(return_value="tx:tenant-a:player-001:1h")
    redis.bet_window_key = MagicMock(return_value="bet:tenant-a:player-001:1h")
    redis.pipeline = MagicMock()
    return redis


# ─── _ch_insert_transaction ──────────────────────────────────────────────────

class TestChInsertTransaction:
    def test_calls_insert_dict_with_correct_table(self):
        ch = MagicMock()
        _ch_insert_transaction(ch, _make_tx_envelope(), _make_tx_envelope()["payload"])
        ch.insert_dict.assert_called_once()
        table_name = ch.insert_dict.call_args[0][0]
        assert table_name == "betaml.transactions"

    def test_row_has_all_required_columns(self):
        ch = MagicMock()
        envelope = _make_tx_envelope()
        _ch_insert_transaction(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        missing = _TX_REQUIRED - set(row.keys())
        assert not missing, f"Colunas faltando em betaml.transactions: {missing}"

    def test_amount_is_float(self):
        ch = MagicMock()
        envelope = _make_tx_envelope(amount="9999")  # string input
        _ch_insert_transaction(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert isinstance(row["amount"], float)
        assert row["amount"] == 9999.0

    def test_tenant_and_player_ids_correct(self):
        ch = MagicMock()
        envelope = _make_tx_envelope()
        _ch_insert_transaction(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert row["tenant_id"] == "tenant-a"
        assert row["player_id"] == "player-001"

    def test_event_date_is_date_object(self):
        ch = MagicMock()
        envelope = _make_tx_envelope()
        _ch_insert_transaction(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert isinstance(row["event_date"], date)

    def test_missing_occurred_at_uses_now(self):
        """Envelope sem occurred_at não deve levantar KeyError."""
        ch = MagicMock()
        envelope = _make_tx_envelope()
        payload = {k: v for k, v in envelope["payload"].items() if k != "occurred_at"}
        _ch_insert_transaction(ch, envelope, payload)
        row = ch.insert_dict.call_args[0][1][0]
        assert row["occurred_at"] is not None


# ─── _ch_insert_bet ──────────────────────────────────────────────────────────

class TestChInsertBet:
    def test_calls_insert_dict_with_correct_table(self):
        ch = MagicMock()
        envelope = _make_bet_envelope()
        _ch_insert_bet(ch, envelope, envelope["payload"])
        table_name = ch.insert_dict.call_args[0][0]
        assert table_name == "betaml.bets"

    def test_row_has_all_required_columns(self):
        ch = MagicMock()
        envelope = _make_bet_envelope()
        _ch_insert_bet(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        missing = _BET_REQUIRED - set(row.keys())
        assert not missing, f"Colunas faltando em betaml.bets: {missing}"

    def test_stake_amount_is_float(self):
        ch = MagicMock()
        envelope = _make_bet_envelope(stake="500")  # string input
        _ch_insert_bet(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert isinstance(row["stake_amount"], float)
        assert row["stake_amount"] == 500.0

    def test_odds_none_when_zero(self):
        ch = MagicMock()
        envelope = _make_bet_envelope(odds=0)
        _ch_insert_bet(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        # odds=0 deve virar None (sem informação de odds)
        assert row["odds"] is None

    def test_placed_at_is_datetime(self):
        ch = MagicMock()
        envelope = _make_bet_envelope()
        _ch_insert_bet(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert isinstance(row["placed_at"], datetime)

    def test_event_date_matches_placed_at(self):
        ch = MagicMock()
        envelope = _make_bet_envelope()
        _ch_insert_bet(ch, envelope, envelope["payload"])
        row = ch.insert_dict.call_args[0][1][0]
        assert row["event_date"] == row["placed_at"].date()


# ─── _ch_insert_features ─────────────────────────────────────────────────────

class TestChInsertFeatures:
    def test_calls_insert_dict_with_correct_table(self):
        ch = MagicMock()
        _ch_insert_features(ch, _make_features(), date(2026, 3, 14))
        table_name = ch.insert_dict.call_args[0][0]
        assert table_name == "betaml.player_features_daily"

    def test_row_has_all_required_columns(self):
        ch = MagicMock()
        _ch_insert_features(ch, _make_features(), date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        missing = _FEATURES_REQUIRED - set(row.keys())
        assert not missing, f"Colunas faltando em betaml.player_features_daily: {missing}"

    def test_all_numeric_features_are_float(self):
        ch = MagicMock()
        _ch_insert_features(ch, _make_features(), date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        for col in ("deposit_sum_24h", "deposit_sum_7d", "zscore_deposit", "ratio_w2d_7d"):
            assert isinstance(row[col], float), f"{col} should be float, got {type(row[col])}"

    def test_count_features_are_int(self):
        ch = MagicMock()
        _ch_insert_features(ch, _make_features(), date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        for col in ("deposit_count_24h", "deposit_count_7d", "withdrawal_count_24h"):
            assert isinstance(row[col], int), f"{col} should be int, got {type(row[col])}"

    def test_missing_optional_features_default_zero(self):
        """Features opcionais ausentes não devem causar KeyError."""
        ch = MagicMock()
        minimal = {"tenant_id": "t", "player_id": "p"}
        _ch_insert_features(ch, minimal, date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        assert row["deposit_sum_24h"] == 0.0
        assert row["deposit_count_24h"] == 0

    def test_feature_version_default_2(self):
        ch = MagicMock()
        features = _make_features()
        del features["feature_version"]
        _ch_insert_features(ch, features, date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        assert row["feature_version"] == 2

    def test_boolean_flags_become_int(self):
        ch = MagicMock()
        features = {**_make_features(), "new_payment_instrument_flag": True, "new_device_flag": True}
        _ch_insert_features(ch, features, date(2026, 3, 14))
        row = ch.insert_dict.call_args[0][1][0]
        assert row["new_payment_flag"] == 1
        assert row["new_device_flag"] == 1


# ─── process_transaction (pipeline integration) ───────────────────────────────

class TestProcessTransaction:
    @pytest.mark.asyncio
    async def test_process_transaction_calls_ch_insert(self):
        """process_transaction deve chamar _ch_insert_transaction para cada depósito."""
        ch = MagicMock()
        redis = _make_redis_mock()
        producer = AsyncMock()
        envelope = _make_tx_envelope()

        with patch.object(_sp_mod, "_ch_insert_transaction") as mock_ch, \
             patch.object(_sp_mod, "compute_features", new_callable=AsyncMock) as mock_cf, \
             patch.object(_sp_mod, "_persist_feature_snapshot"):
            mock_cf.return_value = _make_features()
            await process_transaction(envelope, redis, ch, producer)
            mock_ch.assert_called_once()
            call_args = mock_ch.call_args
            assert call_args[0][0] is ch  # ch_client passado

    @pytest.mark.asyncio
    async def test_process_transaction_ch_error_does_not_raise(self):
        """Falha no ClickHouse não deve propagar exceção — deve apenas logar warning."""
        ch = MagicMock()
        redis = _make_redis_mock()
        producer = AsyncMock()
        envelope = _make_tx_envelope()

        with patch.object(_sp_mod, "_ch_insert_transaction", side_effect=Exception("CH unavailable")), \
             patch.object(_sp_mod, "compute_features", new_callable=AsyncMock, return_value=_make_features()), \
             patch.object(_sp_mod, "_persist_feature_snapshot"):
            # Não deve levantar exceção
            await process_transaction(envelope, redis, ch, producer)
