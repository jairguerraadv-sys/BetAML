"""
Stream Processor — BetAML
Consome canonical.transactions / canonical.bets / canonical.device_events
Calcula features em janelas (24h/7d/30d), baseline incremental,
correlações (device/shared), e grava no Redis (online) + ClickHouse (Gold).
Também publicas features.player_daily e scoring.alerts (candidatos).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

import structlog

sys.path.insert(0, "/app/libs")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logger = structlog.get_logger()

KAFKA_SERVERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CH_HOST        = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT        = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CH_DB          = os.getenv("CLICKHOUSE_DB", "betaml")

TOPICS = [
    "canonical.transactions",
    "canonical.bets",
    "canonical.device_events",
]

# ──────────────────────────────────────────────────
# In-memory rolling windows (for a single-instance dev setup)
# Em prod: use Redis Sorted Sets ou Flink
# ──────────────────────────────────────────────────

# key: (tenant_id, player_id) → list of (timestamp, amount, type)
_txn_window: dict[tuple, list] = defaultdict(list)
_bet_window: dict[tuple, list] = defaultdict(list)
# key: device_id → set of player_ids
_device_players: dict[str, set] = defaultdict(set)
# key: holder_document → set of player_ids
_bank_players: dict[str, set] = defaultdict(set)


def _trim_window(events: list, cutoff: datetime) -> list:
    return [e for e in events if e["ts"] >= cutoff]


async def compute_features(
    tenant_id: str,
    player_id: str,
    redis_client,
    ch_client,
) -> dict:
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d  = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    key = (tenant_id, player_id)

    # Trim windows
    txns = _trim_window(_txn_window.get(key, []), cutoff_30d)
    bets = _trim_window(_bet_window.get(key, []), cutoff_7d)
    _txn_window[key] = txns

    def filter_type(events, type_, since):
        return [e for e in events if e.get("type") == type_ and e["ts"] >= since]

    deposits_24h    = filter_type(txns, "DEPOSIT",    cutoff_24h)
    deposits_7d     = filter_type(txns, "DEPOSIT",    cutoff_7d)
    deposits_30d    = filter_type(txns, "DEPOSIT",    cutoff_30d)
    withdrawal_24h  = filter_type(txns, "WITHDRAWAL", cutoff_24h)
    withdrawal_7d   = filter_type(txns, "WITHDRAWAL", cutoff_7d)
    failed_24h      = [e for e in txns if e.get("status") == "FAILED" and e["ts"] >= cutoff_24h]
    chargebacks_30d = filter_type(txns, "CHARGEBACK", cutoff_30d)
    bets_24h        = [b for b in bets if b["ts"] >= cutoff_24h]
    bets_7d         = bets  # already trimmed to 7d

    dep_sum_24h  = sum(e["amount"] for e in deposits_24h)
    dep_sum_7d   = sum(e["amount"] for e in deposits_7d)
    dep_sum_30d  = sum(e["amount"] for e in deposits_30d)
    with_sum_24h = sum(e["amount"] for e in withdrawal_24h)
    with_sum_7d  = sum(e["amount"] for e in withdrawal_7d)
    bet_sum_24h  = sum(b["amount"] for b in bets_24h)
    bet_sum_7d   = sum(b["amount"] for b in bets)

    # Baseline (historical deposits — rolling mean/std)
    daily_deps: dict[str, float] = defaultdict(float)
    for e in deposits_30d:
        day = e["ts"].date().isoformat()
        daily_deps[day] += float(e["amount"])
    vals = list(daily_deps.values())
    if vals:
        import statistics
        baseline_avg = statistics.mean(vals)
        baseline_std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    else:
        baseline_avg = 0.0
        baseline_std = 0.0

    zscore = (dep_sum_24h - baseline_avg) / max(baseline_std, 1e-9) if baseline_std > 0 else 0.0
    ratio_w2d = (with_sum_7d / max(dep_sum_7d, 1e-9)) if dep_sum_7d > 0 else 0.0

    # ── M2: New features ─────────────────────────────────────────────────────

    # 1. Deposit velocity = deposits per hour in 24h window
    dep_velocity = len(deposits_24h) / 24.0

    # 2. Unique payment instruments in 7d
    unique_instruments_7d = len({e.get("method", "UNK") for e in deposits_7d})

    # 3. Night activity ratio (22:00–06:00)
    def _is_night(e: dict) -> bool:
        h = e["ts"].hour
        return h >= 22 or h < 6

    night_txns = [e for e in txns if e["ts"] >= cutoff_7d and _is_night(e)]
    txns_7d_all = [e for e in txns if e["ts"] >= cutoff_7d]
    night_ratio = len(night_txns) / max(len(txns_7d_all), 1)

    # 4. Weekend activity ratio
    def _is_weekend(e: dict) -> bool:
        return e["ts"].weekday() >= 5

    weekend_txns = [e for e in txns_7d_all if _is_weekend(e)]
    weekend_ratio = len(weekend_txns) / max(len(txns_7d_all), 1)

    # 5. Average odds on bets (7d)
    odds_vals = [b.get("odds") for b in bets_7d if b.get("odds") is not None]
    avg_odds_7d = (sum(odds_vals) / len(odds_vals)) if odds_vals else None

    # 6. Win/loss ratio (30d bets)
    wins_30d  = [b for b in bets if b.get("outcome") == "WIN"]
    losses_30d = [b for b in bets if b.get("outcome") == "LOSS"]
    win_loss_30d = (len(wins_30d) / max(len(losses_30d), 1)) if losses_30d else None

    # 7. Average time (hours) between deposit and withdrawal (7d)
    dep_ts_7d  = sorted(e["ts"] for e in deposits_7d)
    with_ts_7d = sorted(e["ts"] for e in withdrawal_7d)
    if dep_ts_7d and with_ts_7d:
        paired_diffs = []
        for d_ts in dep_ts_7d:
            subsequent_w = [w for w in with_ts_7d if w > d_ts]
            if subsequent_w:
                paired_diffs.append((subsequent_w[0] - d_ts).total_seconds() / 3600)
        avg_dep_to_wdraw_h = (sum(paired_diffs) / len(paired_diffs)) if paired_diffs else None
    else:
        avg_dep_to_wdraw_h = None

    # 8. Multi-currency flag (any non-BRL currency)
    currencies = {e.get("currency", "BRL") for e in txns_7d_all}
    multi_currency = len(currencies) > 1

    # 9. Chargeback rate (30d) = chargebacks / deposits
    chargeback_rate_30d = len(chargebacks_30d) / max(len(deposits_30d), 1)

    # 10. Bonus-to-real ratio (30d) = deposits of type BONUS / total deposits
    bonus_30d = filter_type(txns, "BONUS", cutoff_30d)
    bonus_ratio_30d = len(bonus_30d) / max(len(deposits_30d) + len(bonus_30d), 1)

    # 11. Cashout ratio (7d) = withdrawals / deposits
    cashout_ratio_7d = with_sum_7d / max(dep_sum_7d, 1e-9)

    # ── Network features ─────────────────────────────────────────────────────
    shared_device_count = max(
        (len(_device_players.get(did, set())) for did in _device_players if player_id in _device_players[did]),
        default=0,
    )
    shared_bank_count = max(
        (len(_bank_players.get(doc, set())) for doc in _bank_players if player_id in _bank_players[doc]),
        default=0,
    )

    # Shared instrument score: weighted combination
    shared_instrument_score = min(
        (shared_device_count * 0.4 + shared_bank_count * 0.6) / 10.0, 1.0
    )

    features = {
        "player_id":     player_id,
        "tenant_id":     tenant_id,
        "computed_at":   now.isoformat(),
        "feature_version": 2,           # bumped for M2

        # v1 features
        "deposit_sum_24h":                      float(dep_sum_24h),
        "deposit_sum_7d":                       float(dep_sum_7d),
        "deposit_sum_30d":                      float(dep_sum_30d),
        "deposit_count_24h":                    len(deposits_24h),
        "deposit_count_7d":                     len(deposits_7d),
        "withdrawal_sum_24h":                   float(with_sum_24h),
        "withdrawal_sum_7d":                    float(with_sum_7d),
        "withdrawal_count_24h":                 len(withdrawal_24h),
        "bet_stake_sum_24h":                    float(bet_sum_24h),
        "bet_stake_sum_7d":                     float(bet_sum_7d),
        "ratio_withdrawal_to_deposit_7d":       float(ratio_w2d),
        "baseline_avg_daily_deposit":           float(baseline_avg),
        "baseline_stddev_deposit":              float(baseline_std),
        "zscore_current_deposit_vs_baseline":   float(zscore),
        "new_payment_instrument_flag":          False,
        "new_device_flag":                      False,
        "shared_device_count":                  shared_device_count,
        "shared_bank_account_count":            shared_bank_count,
        "chargeback_count_30d":                 len(chargebacks_30d),
        "failed_deposit_count_24h":             len(failed_24h),

        # v2 new features
        "deposit_velocity":                     float(dep_velocity),
        "unique_instruments_7d":                unique_instruments_7d,
        "night_activity_ratio":                 float(night_ratio),
        "weekend_activity_ratio":               float(weekend_ratio),
        "avg_odds_bet_7d":                      float(avg_odds_7d) if avg_odds_7d is not None else None,
        "win_loss_ratio_30d":                   float(win_loss_30d) if win_loss_30d is not None else None,
        "avg_deposit_to_withdrawal_hours":      float(avg_dep_to_wdraw_h) if avg_dep_to_wdraw_h is not None else None,
        "multi_currency_flag":                  multi_currency,
        "chargeback_rate_30d":                  float(chargeback_rate_30d),
        "bonus_to_real_ratio_30d":              float(bonus_ratio_30d),
        "cashout_ratio_7d":                     float(cashout_ratio_7d),

        # network
        "shared_instrument_score":              float(shared_instrument_score),
    }

    # Persist to Redis (online store, TTL 4h)
    redis_key = f"betaml:{tenant_id}:features:{player_id}"
    await redis_client.hset_dict(redis_key, {k: str(v) for k, v in features.items()}, ttl=14400)

    # Persist to ClickHouse (Gold — async via thread)
    try:
        await asyncio.to_thread(_ch_insert_features, ch_client, features, now.date())
    except Exception as e:
        logger.warning("ch_insert_features_failed", error=str(e))

    return features


def _ch_insert_features(ch_client, features: dict, feature_date) -> None:
    def _f(k: str, default=0.0):
        v = features.get(k)
        return float(v) if v is not None else default

    row = {
        "tenant_id":              features["tenant_id"],
        "player_id":              features["player_id"],
        "feature_date":           feature_date,
        # v1 features
        "deposit_sum_24h":        _f("deposit_sum_24h"),
        "deposit_sum_7d":         _f("deposit_sum_7d"),
        "deposit_sum_30d":        _f("deposit_sum_30d"),
        "deposit_count_24h":      int(features.get("deposit_count_24h", 0)),
        "deposit_count_7d":       int(features.get("deposit_count_7d", 0)),
        "withdrawal_sum_24h":     _f("withdrawal_sum_24h"),
        "withdrawal_sum_7d":      _f("withdrawal_sum_7d"),
        "withdrawal_count_24h":   int(features.get("withdrawal_count_24h", 0)),
        "bet_stake_sum_24h":      _f("bet_stake_sum_24h"),
        "bet_stake_sum_7d":       _f("bet_stake_sum_7d"),
        "ratio_w2d_7d":           _f("ratio_withdrawal_to_deposit_7d"),
        "baseline_avg_deposit":   _f("baseline_avg_daily_deposit"),
        "baseline_stddev_deposit":_f("baseline_stddev_deposit"),
        "zscore_deposit":         _f("zscore_current_deposit_vs_baseline"),
        "new_payment_flag":       int(bool(features.get("new_payment_instrument_flag", False))),
        "new_device_flag":        int(bool(features.get("new_device_flag", False))),
        "shared_device_count":    int(features.get("shared_device_count", 0)),
        "shared_bank_count":      int(features.get("shared_bank_account_count", 0)),
        "chargeback_count_30d":   int(features.get("chargeback_count_30d", 0)),
        # v2 new features
        "deposit_velocity":           _f("deposit_velocity"),
        "unique_instruments_7d":      int(features.get("unique_instruments_7d", 0)),
        "night_activity_ratio":       _f("night_activity_ratio"),
        "weekend_activity_ratio":     _f("weekend_activity_ratio"),
        "avg_odds_bet_7d":            _f("avg_odds_bet_7d"),
        "win_loss_ratio_30d":         _f("win_loss_ratio_30d"),
        "avg_dep_to_wdraw_hours":     _f("avg_deposit_to_withdrawal_hours"),
        "multi_currency_flag":        int(bool(features.get("multi_currency_flag", False))),
        "chargeback_rate_30d":        _f("chargeback_rate_30d"),
        "bonus_to_real_ratio_30d":    _f("bonus_to_real_ratio_30d"),
        "cashout_ratio_7d":           _f("cashout_ratio_7d"),
        "shared_instrument_score":    _f("shared_instrument_score"),
        "feature_version":            int(features.get("feature_version", 2)),
        "computed_at":                datetime.utcnow(),
    }
    ch_client.insert_dict("betaml.player_features_daily", [row])


async def process_transaction(msg_value: dict, redis_client, ch_client, producer):
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    # Add to rolling window
    key = (tenant_id, player_id)
    try:
        occurred_at = datetime.fromisoformat(payload.get("occurred_at", datetime.utcnow().isoformat()))
    except (ValueError, TypeError):
        occurred_at = datetime.utcnow()

    _txn_window[key].append({
        "ts":     occurred_at,
        "amount": float(payload.get("amount", 0)),
        "type":   payload.get("type", ""),
        "status": payload.get("status", ""),
        "instrument": payload.get("payment_instrument", {}),
    })

    # Track bank account sharing
    instrument = payload.get("payment_instrument") or {}
    holder_doc = instrument.get("holder_document") if isinstance(instrument, dict) else None
    if holder_doc:
        _bank_players[holder_doc].add(player_id)

    # Compute + store features
    features = await compute_features(tenant_id, player_id, redis_client, ch_client)

    # Publish features.player_daily
    await producer.send("features.player_daily", {
        "tenant_id": tenant_id,
        "player_id": player_id,
        "features": features,
        "source_event_id": msg_value.get("event_id"),
    })

    # Insert to ClickHouse transactions table
    try:
        await asyncio.to_thread(_ch_insert_transaction, ch_client, msg_value, payload)
    except Exception as e:
        logger.warning("ch_insert_transaction_failed", error=str(e))


def _ch_insert_transaction(ch_client, envelope: dict, payload: dict) -> None:
    try:
        occurred_at = datetime.fromisoformat(payload.get("occurred_at", datetime.utcnow().isoformat()))
    except Exception:
        occurred_at = datetime.utcnow()
    row = {
        "event_id":         envelope.get("event_id", ""),
        "tenant_id":        envelope.get("tenant_id", ""),
        "source_system":    envelope.get("source_system", ""),
        "source_event_id":  envelope.get("source_event_id", ""),
        "player_id":        payload.get("player_id", ""),
        "transaction_type": payload.get("type", ""),
        "amount":           float(payload.get("amount", 0)),
        "currency":         payload.get("currency", "BRL"),
        "method":           payload.get("method", ""),
        "status":           payload.get("status", ""),
        "occurred_at":      occurred_at,
        "event_date":       occurred_at.date(),
        "created_at":       datetime.utcnow(),
    }
    ch_client.insert_dict("betaml.transactions", [row])


async def process_bet(msg_value: dict, redis_client, ch_client, producer):
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    key = (tenant_id, player_id)
    try:
        placed_at = datetime.fromisoformat(payload.get("placed_at", datetime.utcnow().isoformat()))
    except Exception:
        placed_at = datetime.utcnow()

    _bet_window[key].append({
        "ts":     placed_at,
        "amount": float(payload.get("stake_amount", 0)),
    })

    await compute_features(tenant_id, player_id, redis_client, ch_client)

    try:
        await asyncio.to_thread(_ch_insert_bet, ch_client, msg_value, payload)
    except Exception as e:
        logger.warning("ch_insert_bet_failed", error=str(e))


def _ch_insert_bet(ch_client, envelope: dict, payload: dict) -> None:
    try:
        placed_at = datetime.fromisoformat(payload.get("placed_at", datetime.utcnow().isoformat()))
    except Exception:
        placed_at = datetime.utcnow()
    row = {
        "event_id":       envelope.get("event_id", ""),
        "tenant_id":      envelope.get("tenant_id", ""),
        "source_system":  envelope.get("source_system", ""),
        "player_id":      payload.get("player_id", ""),
        "stake_amount":   float(payload.get("stake_amount", 0)),
        "odds":           float(payload.get("odds") or 0) or None,
        "potential_payout": float(payload.get("potential_payout") or 0) or None,
        "settled_payout": float(payload.get("settled_payout") or 0) or None,
        "market_type":    payload.get("market_type", ""),
        "sport":          payload.get("sport", ""),
        "channel":        payload.get("channel", "WEB"),
        "placed_at":      placed_at,
        "settled_at":     None,
        "event_date":     placed_at.date(),
        "status":         payload.get("status", ""),
        "created_at":     datetime.utcnow(),
    }
    ch_client.insert_dict("betaml.bets", [row])


async def process_device_event(msg_value: dict, redis_client, ch_client, producer):
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    device_id = payload.get("device_id")
    if not device_id:
        return
    if player_id:
        _device_players[device_id].add(player_id)
        # Recompute features if shared device
        tenant_id = msg_value.get("tenant_id")
        if tenant_id:
            await compute_features(tenant_id, player_id, redis_client, ch_client)


async def main():
    from libs.clients import KafkaConsumerClient, KafkaProducerClient, RedisClient, ClickHouseClient

    redis_client = RedisClient(REDIS_URL)
    await redis_client.connect()

    ch_client = ClickHouseClient(host=CH_HOST, port=CH_PORT, database=CH_DB)
    ch_client.connect()

    producer = KafkaProducerClient(KAFKA_SERVERS)
    await producer.start()

    consumer = KafkaConsumerClient(
        topics=TOPICS,
        group_id="stream-processor",
        bootstrap_servers=KAFKA_SERVERS,
    )
    await consumer.start()

    logger.info("stream_processor_started", topics=TOPICS)

    try:
        async for msg in consumer:
            try:
                value = msg.value if isinstance(msg.value, dict) else json.loads(msg.value)
                topic = msg.topic

                if topic == "canonical.transactions":
                    await process_transaction(value, redis_client, ch_client, producer)
                elif topic == "canonical.bets":
                    await process_bet(value, redis_client, ch_client, producer)
                elif topic == "canonical.device_events":
                    await process_device_event(value, redis_client, ch_client, producer)

            except Exception as e:
                logger.error("message_processing_error", topic=msg.topic, error=str(e))

    finally:
        await consumer.stop()
        await producer.stop()
        await redis_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
