"""
Stream Processor — BetAML
Consome canonical.transactions / canonical.bets / canonical.device_events
Calcula features em janelas (1h/24h/7d/30d/90d), baseline incremental,
correlações (device/shared), e grava no Redis (online) + ClickHouse (Gold).
Também publicas features.player_daily e scoring.alerts (candidatos).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timedelta

import structlog

# Garante que 'from libs.xxx import' funcione tanto no Docker (/app/libs montado)
# quanto em desenvolvimento local (raiz do projeto no PYTHONPATH)
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
    "ingest.jobs",
    "ingest.jobs.reprocess",
]

# ──────────────────────────────────────────────────
# Redis Sorted Set helpers para janelas de tempo
# Key: betaml:{tenant_id}:txn:{player_id}
# Score: timestamp Unix epoch (float)
# Value: JSON da entrada
# TTL: 90 dias (7 776 000 s)
# ──────────────────────────────────────────────────

WINDOW_TTL_SECONDS = 90 * 24 * 3600  # 90 dias


async def _zadd_entry(redis_client, key: str, ts: datetime, entry: dict) -> None:
    score = ts.timestamp()
    value = json.dumps(entry, default=str)
    await redis_client.zadd_event(key, score, value, window_ttl=WINDOW_TTL_SECONDS)


async def _zread_window(redis_client, key: str, cutoff: datetime) -> list[dict]:
    members = await redis_client.zrange_by_score(key, cutoff.timestamp())
    result = []
    for m in members:
        try:
            entry = json.loads(m)
            # re-parse ts from ISO string
            ts_raw = entry.get("ts")
            if isinstance(ts_raw, str):
                entry["ts"] = datetime.fromisoformat(ts_raw)
            result.append(entry)
        except Exception:
            pass
    return result


async def compute_features(
    tenant_id: str,
    player_id: str,
    redis_client,
    ch_client,
) -> dict:
    now = datetime.utcnow()
    cutoff_1h = now - timedelta(hours=1)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d  = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)
    cutoff_90d = now - timedelta(days=90)

    # Read from Redis Sorted Sets (distributed, survives restarts)
    txn_key = redis_client.txn_window_key(tenant_id, player_id)
    bet_key = redis_client.bet_window_key(tenant_id, player_id)

    txns = await _zread_window(redis_client, txn_key, cutoff_90d)
    bets = await _zread_window(redis_client, bet_key, cutoff_90d)

    def filter_type(events, type_, since):
        return [e for e in events if e.get("type") == type_ and e["ts"] >= since]

    deposits_24h    = filter_type(txns, "DEPOSIT",    cutoff_24h)
    deposits_7d     = filter_type(txns, "DEPOSIT",    cutoff_7d)
    deposits_30d    = filter_type(txns, "DEPOSIT",    cutoff_30d)
    deposits_90d    = filter_type(txns, "DEPOSIT",    cutoff_90d)
    withdrawal_24h  = filter_type(txns, "WITHDRAWAL", cutoff_24h)
    withdrawal_7d   = filter_type(txns, "WITHDRAWAL", cutoff_7d)
    withdrawal_90d  = filter_type(txns, "WITHDRAWAL", cutoff_90d)
    failed_24h      = [e for e in txns if e.get("status") == "FAILED" and e["ts"] >= cutoff_24h]
    chargebacks_30d = filter_type(txns, "CHARGEBACK", cutoff_30d)
    bets_24h        = [b for b in bets if b["ts"] >= cutoff_24h]
    bets_7d         = [b for b in bets if b["ts"] >= cutoff_7d]
    bets_30d        = [b for b in bets if b["ts"] >= cutoff_30d]
    deposits_1h     = filter_type(txns, "DEPOSIT", cutoff_1h)

    dep_sum_24h  = sum(e["amount"] for e in deposits_24h)
    dep_sum_7d   = sum(e["amount"] for e in deposits_7d)
    dep_sum_30d  = sum(e["amount"] for e in deposits_30d)
    dep_sum_90d  = sum(e["amount"] for e in deposits_90d)
    with_sum_24h = sum(e["amount"] for e in withdrawal_24h)
    with_sum_7d  = sum(e["amount"] for e in withdrawal_7d)
    with_sum_90d = sum(e["amount"] for e in withdrawal_90d)
    bet_sum_24h  = sum(b["amount"] for b in bets_24h)
    bet_sum_7d   = sum(b["amount"] for b in bets)

    # Baseline (historical deposits — rolling mean/std)
    daily_deps: dict[str, float] = {}
    for e in deposits_30d:
        day = e["ts"].date().isoformat()
        daily_deps[day] = daily_deps.get(day, 0.0) + float(e["amount"])
    vals = list(daily_deps.values())
    if vals:
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
    wins_30d  = [b for b in bets_30d if b.get("outcome") == "WIN"]
    losses_30d = [b for b in bets_30d if b.get("outcome") == "LOSS"]
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

    # ── Network features (Redis Sets) ─────────────────────────────────────────
    # player_devices: set of device_ids this player used
    player_devs = await redis_client.smembers_set(
        redis_client.player_devices_key(tenant_id, player_id)
    )
    # For each device, count how many players share it
    dev_counts = []
    for did in player_devs:
        c = await redis_client.scard_set(redis_client.device_members_key(tenant_id, did))
        dev_counts.append(c)
    shared_device_count = max(dev_counts, default=1) - 1  # exclude self

    # player_banks: set of bank-doc-hashes this player used
    player_banks = await redis_client.smembers_set(
        redis_client.player_banks_key(tenant_id, player_id)
    )
    bank_counts = []
    for doc_hash in player_banks:
        c = await redis_client.scard_set(redis_client.bank_members_key(tenant_id, doc_hash))
        bank_counts.append(c)
    shared_bank_count = max(bank_counts, default=1) - 1  # exclude self

    shared_device_score = min(max(shared_device_count, 0) / 10.0, 1.0)

    # Shared instrument score: weighted combination
    shared_instrument_score = min(
        (shared_device_count * 0.4 + shared_bank_count * 0.6) / 10.0, 1.0
    )

    cluster_size = max(max(dev_counts, default=1), max(bank_counts, default=1))
    cluster_seed = sorted(str(v) for v in player_devs.union(player_banks))
    cluster_id = (
        f"cluster:{hashlib.sha1('|'.join(cluster_seed).encode()).hexdigest()[:12]}"
        if cluster_seed
        else f"solo:{player_id}"
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
        "deposit_sum_90d":                      float(dep_sum_90d),
        "deposit_count_1h":                     len(deposits_1h),
        "deposit_count_24h":                    len(deposits_24h),
        "deposit_count_7d":                     len(deposits_7d),
        "deposit_count_90d":                    len(deposits_90d),
        "withdrawal_sum_24h":                   float(with_sum_24h),
        "withdrawal_sum_7d":                    float(with_sum_7d),
        "withdrawal_sum_90d":                   float(with_sum_90d),
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
        "unique_instruments_used_7d":           unique_instruments_7d,
        "night_activity_ratio":                 float(night_ratio),
        "weekend_activity_ratio":               float(weekend_ratio),
        "avg_odds_bet_7d":                      float(avg_odds_7d) if avg_odds_7d is not None else None,
        "win_loss_ratio_30d":                   float(win_loss_30d) if win_loss_30d is not None else None,
        "avg_deposit_to_withdrawal_hours":      float(avg_dep_to_wdraw_h) if avg_dep_to_wdraw_h is not None else None,
        "avg_time_between_deposit_and_withdrawal_7d": float(avg_dep_to_wdraw_h) if avg_dep_to_wdraw_h is not None else None,
        "multi_currency_flag":                  multi_currency,
        "chargeback_rate_30d":                  float(chargeback_rate_30d),
        "bonus_to_real_ratio_30d":              float(bonus_ratio_30d),
        "bonus_to_real_money_ratio_30d":        float(bonus_ratio_30d),
        "cashout_ratio_7d":                     float(cashout_ratio_7d),

        # network
        "shared_device_score":                  float(shared_device_score),
        "shared_instrument_score":              float(shared_instrument_score),
        "cluster_id":                           cluster_id,
        "cluster_size":                         int(cluster_size),
    }

    # Persist to Redis (online store, TTL 4h)
    redis_key = f"betaml:{tenant_id}:features:{player_id}"
    await redis_client.hset_dict(redis_key, {k: str(v) for k, v in features.items()}, ttl=14400)

    # Persist to ClickHouse (Gold — async via thread)
    try:
        await asyncio.to_thread(_ch_insert_features, ch_client, features, now.date())
    except Exception as e:
        logger.warning("ch_insert_features_failed", error=str(e))

    try:
        await asyncio.to_thread(_persist_feature_snapshot, features, now.date())
    except Exception as e:
        logger.warning("feature_snapshot_persist_failed", error=str(e), player_id=player_id)

    try:
        await asyncio.to_thread(_persist_feature_snapshot, features, now.date())
    except Exception as e:
        logger.warning("feature_snapshot_persist_failed", error=str(e), player_id=player_id)

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


def _persist_feature_snapshot(features: dict, feature_date) -> None:
    import io
    import sqlalchemy as sa

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = sa.create_engine(sync_url, pool_pre_ping=True)
    payload = json.dumps(features, ensure_ascii=False, default=str)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO feature_snapshots (
                        id,
                        tenant_id,
                        player_id,
                        feature_date,
                        snapshot_date,
                        features,
                        created_at
                    ) VALUES (
                        :id,
                        :tenant_id,
                        :player_id,
                        :feature_date,
                        :snapshot_date,
                        CAST(:features AS jsonb),
                        NOW()
                    )
                    ON CONFLICT (tenant_id, player_id, feature_date)
                    DO UPDATE SET
                        snapshot_date = EXCLUDED.snapshot_date,
                        features = EXCLUDED.features,
                        created_at = NOW()
                    """
                ),
                {
                    "id": str(__import__("uuid").uuid4()),
                    "tenant_id": features["tenant_id"],
                    "player_id": features["player_id"],
                    "feature_date": feature_date,
                    "snapshot_date": feature_date,
                    "features": payload,
                },
            )
    finally:
        engine.dispose()

    try:
        from minio import Minio

        endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000").replace("http://", "").replace("https://", "")
        secure = os.getenv("MINIO_ENDPOINT", "http://minio:9000").startswith("https://")
        bucket = os.getenv("MINIO_BUCKET", "betaml-lakehouse")
        client = Minio(
            endpoint,
            access_key=os.getenv("MINIO_ACCESS_KEY", "minio"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
            secure=secure,
        )
        object_name = (
            f"gold/{features['tenant_id']}/feature_date={feature_date.isoformat()}/"
            f"entity_type=player/player_id={features['player_id']}.json"
        )
        encoded = payload.encode("utf-8")
        stream = io.BytesIO(encoded)
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
        client.put_object(bucket, object_name, stream, length=len(encoded), content_type="application/json")
    except Exception as exc:  # noqa: BLE001
        logger.warning("gold_snapshot_persist_failed", error=str(exc), player_id=features.get("player_id"))


async def process_transaction(msg_value: dict, redis_client, ch_client, producer):
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    try:
        occurred_at = datetime.fromisoformat(payload.get("occurred_at", datetime.utcnow().isoformat()))
    except (ValueError, TypeError):
        occurred_at = datetime.utcnow()

    # Add to Redis Sorted Set window
    entry = {
        "ts":         occurred_at.isoformat(),
        "amount":     float(payload.get("amount", 0)),
        "type":       payload.get("type", ""),
        "status":     payload.get("status", ""),
        "method":     payload.get("method", ""),
        "currency":   payload.get("currency", "BRL"),
    }
    txn_key = redis_client.txn_window_key(tenant_id, player_id)
    await _zadd_entry(redis_client, txn_key, occurred_at, entry)

    # Track bank account sharing via Redis Sets
    instrument = payload.get("payment_instrument") or {}
    holder_doc = instrument.get("holder_document") if isinstance(instrument, dict) else None
    if holder_doc:
        bank_key   = redis_client.bank_members_key(tenant_id, holder_doc)
        pbank_key  = redis_client.player_banks_key(tenant_id, player_id)
        await redis_client.sadd_member(bank_key, player_id)
        await redis_client.sadd_member(pbank_key, holder_doc)

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

    try:
        placed_at = datetime.fromisoformat(payload.get("placed_at", datetime.utcnow().isoformat()))
    except Exception:
        placed_at = datetime.utcnow()

    entry = {
        "ts":      placed_at.isoformat(),
        "amount":  float(payload.get("stake_amount", 0)),
        "odds":    payload.get("odds"),
        "outcome": payload.get("outcome"),
    }
    bet_key = redis_client.bet_window_key(tenant_id, player_id)
    await _zadd_entry(redis_client, bet_key, placed_at, entry)

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
    tenant_id = msg_value.get("tenant_id")
    if not device_id or not tenant_id:
        return
    if player_id:
        # Track device→players and player→devices in Redis Sets
        dev_key  = redis_client.device_members_key(tenant_id, device_id)
        pdev_key = redis_client.player_devices_key(tenant_id, player_id)
        await redis_client.sadd_member(dev_key, player_id)
        await redis_client.sadd_member(pdev_key, device_id)
        # Recompute features if shared device
        await compute_features(tenant_id, player_id, redis_client, ch_client)


async def process_ingest_job(msg_value: dict, redis_client, ch_client, producer) -> None:
    """
    Consome mensagem de ingest.jobs:
      - Decodifica conteúdo do arquivo (base64 CSV ou JSON)
      - Aplica MappingConfig (via MappingEngine) se mapping_config_id fornecido
      - Publica eventos em raw.{entity_type}s
      - Atualiza IngestJob no Postgres com DONE/FAILED + contagens
    """
    import base64
    import csv
    import io
    import sqlalchemy as sa
    from minio import Minio

    from libs.mapping import MappingEngine

    job_id = msg_value.get("job_id")
    tenant_id = msg_value.get("tenant_id")
    source_system = msg_value.get("source_system", "")
    mapping_config_id = msg_value.get("mapping_config_id")
    file_content_b64 = msg_value.get("file_content_b64", "")
    file_path = msg_value.get("file_path")

    if not job_id or not tenant_id:
        logger.warning("ingest_job_missing_fields", msg=msg_value)
        return

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")

    def _update_job(
        status: str,
        total: int,
        processed: int,
        failed: int,
        error_msg: str | None = None,
        *,
        bytes_processed: int = 0,
        duration_ms: int | None = None,
        error_sample: list[dict[str, object]] | None = None,
    ):
        engine = sa.create_engine(sync_url, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    UPDATE ingest_jobs
                    SET status = :s,
                        total_records = :t,
                        processed_records = :p,
                        failed_records = :f,
                        error_message = :e,
                        bytes_processed = :bp,
                        duration_ms = :dur,
                        error_sample = CAST(:es AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "s": status,
                    "t": total,
                    "p": processed,
                    "f": failed,
                    "e": error_msg,
                    "id": job_id,
                    "bp": bytes_processed,
                    "dur": duration_ms,
                    "es": json.dumps(error_sample or [], ensure_ascii=False),
                },
            )
        engine.dispose()

    def _insert_ingest_error(*, raw_payload: dict, line_number: int, reason: str):
        engine = sa.create_engine(sync_url, pool_pre_ping=True)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO ingest_errors (
                        id,
                        tenant_id,
                        ingest_job_id,
                        source_system,
                        entity_type,
                        raw_payload,
                        error_reason,
                        error_detail,
                        line_number,
                        resolved
                    ) VALUES (
                        :id,
                        :tenant_id,
                        :ingest_job_id,
                        :source_system,
                        :entity_type,
                        :raw_payload,
                        :error_reason,
                        CAST(:error_detail AS jsonb),
                        :line_number,
                        false
                    )
                    """
                ),
                {
                    "id": str(__import__("uuid").uuid4()),
                    "tenant_id": tenant_id,
                    "ingest_job_id": job_id,
                    "source_system": source_system,
                    "entity_type": "TRANSACTION",
                    "raw_payload": json.dumps(raw_payload, ensure_ascii=False),
                    "error_reason": reason,
                    "error_detail": json.dumps({"line_number": line_number}, ensure_ascii=False),
                    "line_number": line_number,
                },
            )
        engine.dispose()

    try:
        if file_content_b64:
            content = base64.b64decode(file_content_b64).decode("utf-8", errors="replace")
        elif file_path:
            endpoint = os.getenv("MINIO_ENDPOINT", "http://minio:9000").replace("http://", "").replace("https://", "")
            secure = os.getenv("MINIO_ENDPOINT", "http://minio:9000").startswith("https://")
            bucket = os.getenv("MINIO_BUCKET", "betaml-lakehouse")
            client = Minio(
                endpoint,
                access_key=os.getenv("MINIO_ACCESS_KEY", "minio"),
                secret_key=os.getenv("MINIO_SECRET_KEY", "minio123"),
                secure=secure,
            )
            obj = client.get_object(bucket, file_path)
            try:
                content = obj.read().decode("utf-8", errors="replace")
            finally:
                obj.close()
                obj.release_conn()
        else:
            raise ValueError("missing file_content_b64 and file_path")
    except Exception as exc:
        await asyncio.to_thread(_update_job, "FAILED", 0, 0, 0, f"file load error: {exc}")
        return

    mapping_cfg: dict | None = None
    if mapping_config_id:
        try:
            engine = sa.create_engine(sync_url, pool_pre_ping=True)
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT config_json FROM mapping_configs WHERE id = :id"),
                    {"id": mapping_config_id},
                ).fetchone()
            mapping_cfg = dict(row._mapping)["config_json"] if row else None
            engine.dispose()
        except Exception:
            mapping_cfg = None

    mapper: MappingEngine | None = None
    if isinstance(mapping_cfg, dict):
        try:
            mapper = MappingEngine(mapping_cfg)
        except Exception as exc:
            logger.warning("ingest_job_invalid_mapping", job_id=job_id, error=str(exc))

    rows: list[dict] = []
    try:
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
    except Exception:
        for line in content.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass

    total = len(rows)
    processed = 0
    failed = 0
    bytes_processed = 0
    error_sample: list[dict[str, object]] = []
    started_at = datetime.utcnow()

    await asyncio.to_thread(_update_job, "PROCESSING", total, 0, 0)

    max_retries = int(os.getenv("DLQ_MAX_RETRIES", "3"))

    async def _publish_with_retries(topic: str, envelope: dict, key: str, row_data: dict, line_number: int) -> bool:
        for attempt in range(1, max_retries + 1):
            try:
                await producer.send(topic, envelope, key=key)
                return True
            except Exception as pub_exc:  # noqa: BLE001
                if attempt >= max_retries:
                    await producer.send(
                        f"{topic}.dlq",
                        {
                            "tenant_id": tenant_id,
                            "job_id": job_id,
                            "source_system": source_system,
                            "line_number": line_number,
                            "reason": str(pub_exc),
                            "attempt": attempt,
                            "max_retries": max_retries,
                            "failed_at": datetime.utcnow().isoformat(),
                            "target_topic": topic,
                            "raw_payload": row_data,
                        },
                        key=str(job_id),
                    )
                    return False
                await asyncio.sleep(0.1 * attempt)
        return False

    for idx, row_data in enumerate(rows, start=1):
        try:
            payload = mapper.apply(row_data) if mapper else row_data

            entity_type = str(
                payload.get("entity_type")
                or (mapping_cfg.get("entity_type", "transaction") if mapping_cfg else "transaction")
            ).lower()

            envelope = {
                "event_id": str(__import__("uuid").uuid4()),
                "tenant_id": tenant_id,
                "source_system": source_system,
                "source_event_id": payload.get("id") or payload.get("external_id", ""),
                "schema_version": 1,
                "entity_type": entity_type,
                "occurred_at": datetime.utcnow().isoformat(),
                "payload": payload,
                "raw_payload": row_data,
                "ingest_metadata": {"job_id": job_id, "source": "file"},
            }
            topic = f"raw.{entity_type}s"
            ok = await _publish_with_retries(
                topic,
                envelope,
                envelope["source_event_id"] or envelope["event_id"],
                row_data,
                idx,
            )
            if ok:
                processed += 1
                bytes_processed += len(json.dumps(row_data, ensure_ascii=False).encode("utf-8"))
            else:
                failed += 1
                if len(error_sample) < 10:
                    error_sample.append({"line": idx, "reason": "publish_failed_after_retries", "raw": row_data})
                await asyncio.to_thread(
                    _insert_ingest_error,
                    raw_payload=row_data,
                    line_number=idx,
                    reason="publish_failed_after_retries",
                )
        except Exception as exc:
            reason = str(exc)
            logger.warning("ingest_row_failed", job_id=job_id, line=idx, error=reason)
            failed += 1
            if len(error_sample) < 10:
                error_sample.append({"line": idx, "reason": reason, "raw": row_data})

            await asyncio.to_thread(_insert_ingest_error, raw_payload=row_data, line_number=idx, reason=reason)

            try:
                await producer.send(
                    "raw.transactions.dlq",
                    {
                        "tenant_id": tenant_id,
                        "job_id": job_id,
                        "source_system": source_system,
                        "line_number": idx,
                        "reason": reason,
                        "attempt": max_retries,
                        "max_retries": max_retries,
                        "failed_at": datetime.utcnow().isoformat(),
                        "target_topic": "raw.transactions",
                        "raw_payload": row_data,
                    },
                    key=str(job_id),
                )
            except Exception as dlq_exc:
                logger.warning("dlq_publish_failed", job_id=job_id, error=str(dlq_exc))

    final_status = "DONE" if failed == 0 else ("PARTIAL" if processed > 0 else "FAILED")
    duration_ms = int((datetime.utcnow() - started_at).total_seconds() * 1000)
    await asyncio.to_thread(
        _update_job,
        final_status,
        total,
        processed,
        failed,
        None,
        bytes_processed=bytes_processed,
        duration_ms=duration_ms,
        error_sample=error_sample,
    )
    logger.info(
        "ingest_job_complete",
        job_id=job_id,
        total=total,
        processed=processed,
        failed=failed,
        status=final_status,
    )


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
                elif topic == "ingest.jobs":
                    await process_ingest_job(value, redis_client, ch_client, producer)
                elif topic == "ingest.jobs.reprocess":
                    await process_ingest_job(value, redis_client, ch_client, producer)

            except Exception as e:
                logger.error("message_processing_error", topic=msg.topic, error=str(e))

    finally:
        await consumer.stop()
        await producer.stop()
        await redis_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())


# ──────────────────────────────────────────────────────────────────────────────
# Synchronous helper — used by unit tests (no Redis/ClickHouse required)
# ──────────────────────────────────────────────────────────────────────────────

def compute_features_offline(player_id: str, history: dict) -> dict:
    """
    Pure synchronous feature computation from a pre-loaded history dict.
    Accepts history = {"transactions": [{"amount", "txn_type", "currency",
    "instrument_id", "created_at", "is_chargeback", "result", ...}]}
    """
    from datetime import datetime as _dt, timedelta as _td

    txns_raw = history.get("transactions", [])
    now = _dt.utcnow()
    cutoff_24h = now - _td(hours=24)
    cutoff_7d  = now - _td(days=7)
    cutoff_30d = now - _td(days=30)
    cutoff_90d = now - _td(days=90)

    def _parse_ts(t: dict) -> _dt:
        ts = t.get("created_at", "")
        if isinstance(ts, _dt):
            return ts
        try:
            return _dt.fromisoformat(str(ts).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return now

    txns = [{"ts": _parse_ts(t), **t} for t in txns_raw]

    def in_win(t: dict, since: _dt) -> bool:
        return t["ts"] >= since

    deposits_24h = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_24h)]
    deposits_30d = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_30d)]
    deposits_90d = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_90d)]
    txns_24h     = [t for t in txns if in_win(t, cutoff_24h)]
    txns_7d      = [t for t in txns if in_win(t, cutoff_7d)]
    txns_30d     = [t for t in txns if in_win(t, cutoff_30d)]
    txns_90d     = [t for t in txns if in_win(t, cutoff_90d)]

    # Deposit velocity = deposits per hour in 24h
    dep_velocity = len(deposits_24h) / 24.0

    # Unique payment instruments in 7d
    unique_instruments_7d = len({t.get("instrument_id") for t in txns_7d if t.get("instrument_id")})

    # Night activity ratio (22:00–06:00 in 7d)
    def _is_night(t: dict) -> bool:
        h = t["ts"].hour
        return h >= 22 or h < 6

    night_ratio = len([t for t in txns_7d if _is_night(t)]) / max(len(txns_7d), 1)

    # Multi-currency flag
    currencies = {t.get("currency", "BRL") for t in txns_7d}
    multi_currency = len(currencies) > 1

    # Win/loss ratio 30d
    bets_30d   = [t for t in txns_30d if t.get("txn_type") == "BET"]
    wins_30d   = [b for b in bets_30d if b.get("result") == "WIN"]
    losses_30d = [b for b in bets_30d if b.get("result") == "LOSS"]
    win_loss_30d = len(wins_30d) / max(len(losses_30d), 1)

    # Chargeback rate 30d
    chargebacks_30d  = [t for t in txns_30d if t.get("is_chargeback")]
    chargeback_rate  = len(chargebacks_30d) / max(len(deposits_30d), 1)

    devices = sorted({t.get("device_id") for t in txns_90d if t.get("device_id")})
    banks = sorted({t.get("bank_id") for t in txns_90d if t.get("bank_id")})
    cluster_seed = devices + banks
    cluster_id = (
        f"cluster:{hashlib.sha1('|'.join(cluster_seed).encode()).hexdigest()[:12]}"
        if cluster_seed
        else f"solo:{player_id}"
    )

    return {
        "player_id":             player_id,
        "feature_version":       2,
        "computed_at":           now.isoformat(),
        "deposit_velocity":      float(dep_velocity),
        "deposit_count_1h":      len([t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, now - _td(hours=1))]),
        "deposit_count_24h":     len(deposits_24h),
        "deposit_sum_24h":       float(sum(t.get("amount", 0) for t in deposits_24h)),
        "deposit_sum_90d":       float(sum(t.get("amount", 0) for t in deposits_90d)),
        "unique_instruments_7d": unique_instruments_7d,
        "unique_instruments_used_7d": unique_instruments_7d,
        "night_activity_ratio":  float(night_ratio),
        "multi_currency_flag":   multi_currency,
        "win_loss_ratio_30d":    float(win_loss_30d),
        "chargeback_rate_30d":   float(chargeback_rate),
        "avg_time_between_deposit_and_withdrawal_7d": None,
        "bonus_to_real_money_ratio_30d": 0.0,
        "shared_device_score":   0.0,
        "shared_instrument_score": 0.0,
        "cluster_id":            cluster_id,
        "cluster_size":          max(len(devices), len(banks), 1),
        "txn_count_24h":         len(txns_24h),
    }
