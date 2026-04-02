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
import time
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from prometheus_client import Counter, Gauge, Histogram, REGISTRY, start_http_server

# Garante que 'from libs.xxx import' funcione tanto no Docker (/app/libs montado)
# quanto em desenvolvimento local (raiz do projeto no PYTHONPATH)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from libs.telemetry import init_opentelemetry_stub

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if os.getenv("ENVIRONMENT", "development").lower() in {"development", "test"}
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()

KAFKA_SERVERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CH_HOST        = os.getenv("CLICKHOUSE_HOST", "localhost")
CH_PORT        = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CH_DB          = os.getenv("CLICKHOUSE_DB", "betaml")
METRICS_PORT   = int(os.getenv("METRICS_PORT", "8003"))

TOPICS = [
    "canonical.transactions",
    "canonical.bets",
    "canonical.device_events",
    # GAP-stream: novos tópicos para eventos de ciclo de vida do player
    "canonical.kyc_events",
    "canonical.responsible_gambling_events",
    "canonical.account_status_changes",
    "ingest.jobs",
    "ingest.jobs.reprocess",
]

_oltp_engine = None


def _metric_aliases(name: str) -> list[str]:
    aliases = [name]
    if name.endswith("_total"):
        aliases.append(name[: -len("_total")])
    return aliases


def _get_or_create_metric(metric_cls, name: str, documentation: str, labelnames: list[str]):
    registry_collectors = getattr(REGISTRY, "_names_to_collectors", {})
    for alias in _metric_aliases(name):
        existing = registry_collectors.get(alias)
        if existing is not None:
            return existing
    return metric_cls(name, documentation, labelnames)


EVENTS_PROCESSED = _get_or_create_metric(
    Counter,
    "betaml_stream_events_processed_total",
    "Total de eventos processados pelo stream processor",
    ["topic", "status"],
)

PROCESSING_LATENCY = _get_or_create_metric(
    Histogram,
    "betaml_stream_processing_seconds",
    "Latência de processamento do stream processor por tópico",
    ["topic"],
)

CONSUMER_LAG = _get_or_create_metric(
    Gauge,
    "betaml_stream_consumer_lag_messages",
    "Lag estimado do consumer do stream processor por tópico",
    ["group_id", "topic"],
)


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive_utc_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return _now_naive_utc()
    else:
        return _now_naive_utc()

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _get_oltp_engine():
    import sqlalchemy as sa

    global _oltp_engine
    if _oltp_engine is None:
        db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
        sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
        _oltp_engine = sa.create_engine(sync_url, pool_pre_ping=True)
    return _oltp_engine


def _resolve_player_id_sync(conn, tenant_id: str, raw_player_id: object) -> str | None:
    import sqlalchemy as sa

    if raw_player_id is None:
        return None
    pid = str(raw_player_id)
    if _is_uuid(pid):
        return pid
    mapped = conn.execute(
        sa.text(
            """
            SELECT id
            FROM players
            WHERE tenant_id = :tid
              AND (external_player_id = :pid OR external_id = :pid)
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"tid": tenant_id, "pid": pid},
    ).scalar_one_or_none()
    return str(mapped) if mapped else None


def _persist_transaction_oltp(envelope: dict, payload: dict) -> None:
    import sqlalchemy as sa

    tenant_id = str(envelope.get("tenant_id") or "")
    if not tenant_id:
        return
    source_event_id = str(envelope.get("source_event_id") or envelope.get("event_id") or "")
    occurred_at = _to_naive_utc_datetime(payload.get("occurred_at", _iso_now()))
    payment_instrument = payload.get("payment_instrument") if isinstance(payload.get("payment_instrument"), dict) else {}
    holder_document = str(payment_instrument.get("holder_document") or "")
    bank_account_hash = hashlib.sha256(holder_document.encode("utf-8")).hexdigest() if holder_document else None

    engine = _get_oltp_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id})
        exists = conn.execute(
            sa.text(
                """
                SELECT 1 FROM financial_transactions
                WHERE tenant_id = :tid AND source_event_id = :seid
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "seid": source_event_id},
        ).scalar_one_or_none()
        if exists:
            return

        player_id = _resolve_player_id_sync(conn, tenant_id, payload.get("player_id"))
        conn.execute(
            sa.text(
                """
                INSERT INTO financial_transactions (
                    id, tenant_id, player_id, external_tx_id, source_system, type,
                    amount, currency, status, payment_method, payment_instrument,
                    bank_account_hash, source_event_id, raw_payload, occurred_at, created_at
                ) VALUES (
                    :id, :tenant_id, :player_id, :external_tx_id, :source_system, :type,
                    :amount, :currency, :status, :payment_method, :payment_instrument,
                    :bank_account_hash, :source_event_id, CAST(:raw_payload AS jsonb), :occurred_at, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "player_id": player_id,
                "external_tx_id": payload.get("external_transaction_id") or payload.get("external_tx_id"),
                "source_system": envelope.get("source_system") or "unknown",
                "type": str(payload.get("type") or "DEPOSIT").upper(),
                "amount": float(payload.get("amount") or 0),
                "currency": payload.get("currency") or "BRL",
                "status": str(payload.get("status") or "SETTLED").upper(),
                "payment_method": payload.get("method"),
                "payment_instrument": json.dumps(payment_instrument, ensure_ascii=False),
                "bank_account_hash": bank_account_hash,
                "source_event_id": source_event_id,
                "raw_payload": json.dumps(envelope.get("raw_payload") or payload, ensure_ascii=False),
                "occurred_at": occurred_at,
            },
        )


def _persist_bet_oltp(envelope: dict, payload: dict) -> None:
    import sqlalchemy as sa

    tenant_id = str(envelope.get("tenant_id") or "")
    if not tenant_id:
        return
    source_event_id = str(envelope.get("source_event_id") or envelope.get("event_id") or "")
    occurred_at = _to_naive_utc_datetime(payload.get("placed_at") or payload.get("occurred_at") or _iso_now())

    engine = _get_oltp_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id})
        exists = conn.execute(
            sa.text(
                """
                SELECT 1 FROM bets
                WHERE tenant_id = :tid AND source_event_id = :seid
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "seid": source_event_id},
        ).scalar_one_or_none()
        if exists:
            return

        player_id = _resolve_player_id_sync(conn, tenant_id, payload.get("player_id"))
        conn.execute(
            sa.text(
                """
                INSERT INTO bets (
                    id, tenant_id, player_id, external_bet_id, source_system, bet_type,
                    stake_amount, potential_payout, actual_payout, odds, currency,
                    status, event_name, market_name, selection_name, source_event_id,
                    raw_payload, occurred_at, created_at
                ) VALUES (
                    :id, :tenant_id, :player_id, :external_bet_id, :source_system, :bet_type,
                    :stake_amount, :potential_payout, :actual_payout, :odds, :currency,
                    :status, :event_name, :market_name, :selection_name, :source_event_id,
                    CAST(:raw_payload AS jsonb), :occurred_at, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "player_id": player_id,
                "external_bet_id": payload.get("external_bet_id"),
                "source_system": envelope.get("source_system") or "unknown",
                "bet_type": payload.get("bet_type") or "SPORTS",
                "stake_amount": float(payload.get("stake_amount") or 0),
                "potential_payout": float(payload.get("potential_payout") or 0) if payload.get("potential_payout") is not None else None,
                "actual_payout": float(payload.get("settled_payout") or 0) if payload.get("settled_payout") is not None else None,
                "odds": float(payload.get("odds") or 0) if payload.get("odds") is not None else None,
                "currency": payload.get("currency") or "BRL",
                "status": str(payload.get("status") or "OPEN").upper(),
                "event_name": payload.get("sport"),
                "market_name": payload.get("market_type"),
                "selection_name": payload.get("selection"),
                "source_event_id": source_event_id,
                "raw_payload": json.dumps(envelope.get("raw_payload") or payload, ensure_ascii=False),
                "occurred_at": occurred_at,
            },
        )


def _persist_device_event_oltp(envelope: dict, payload: dict) -> None:
    import sqlalchemy as sa

    tenant_id = str(envelope.get("tenant_id") or "")
    if not tenant_id:
        return
    source_event_id = str(envelope.get("source_event_id") or envelope.get("event_id") or "")
    occurred_at = _to_naive_utc_datetime(payload.get("occurred_at") or _iso_now())

    engine = _get_oltp_engine()
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tenant_id})
        exists = conn.execute(
            sa.text(
                """
                SELECT 1 FROM device_events
                WHERE tenant_id = :tid AND source_event_id = :seid
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "seid": source_event_id},
        ).scalar_one_or_none()
        if exists:
            return

        player_id = _resolve_player_id_sync(conn, tenant_id, payload.get("player_id"))
        ip = str(payload.get("ip") or "")
        ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest() if ip else None
        conn.execute(
            sa.text(
                """
                INSERT INTO device_events (
                    id, tenant_id, player_id, external_evt_id, source_system, action,
                    device_id, device_type, device_hash, ip_address, ip_hash, country_code,
                    user_agent, source_event_id, raw_payload, occurred_at, created_at
                ) VALUES (
                    :id, :tenant_id, :player_id, :external_evt_id, :source_system, :action,
                    :device_id, :device_type, :device_hash, :ip_address, :ip_hash, :country_code,
                    :user_agent, :source_event_id, CAST(:raw_payload AS jsonb), :occurred_at, NOW()
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "player_id": player_id,
                "external_evt_id": payload.get("external_evt_id") or payload.get("event_id"),
                "source_system": envelope.get("source_system") or "unknown",
                "action": str(payload.get("action") or "LOGIN").upper(),
                "device_id": payload.get("device_id"),
                "device_type": payload.get("device_type"),
                "device_hash": payload.get("device_id") or payload.get("device_hash"),
                "ip_address": ip or None,
                "ip_hash": ip_hash,
                "country_code": payload.get("country_code"),
                "user_agent": payload.get("user_agent"),
                "source_event_id": source_event_id,
                "raw_payload": json.dumps(envelope.get("raw_payload") or payload, ensure_ascii=False),
                "occurred_at": occurred_at,
            },
        )


def _normalize_transaction_payload(payload: dict) -> dict:
    amount = _coerce_float(payload.get("amount") or payload.get("value"), 0.0)
    tx_type = (
        payload.get("type")
        or payload.get("transaction_type")
        or payload.get("txn_type")
        or "DEPOSIT"
    )
    method = payload.get("method") or payload.get("payment_method") or payload.get("instrument_type")
    status = payload.get("status") or payload.get("txn_status") or "SETTLED"
    occurred_at = payload.get("occurred_at") or payload.get("timestamp") or payload.get("transactionDate") or _iso_now()
    payment_instrument = payload.get("payment_instrument")
    if not isinstance(payment_instrument, dict):
        payment_instrument = {}
    if not payment_instrument and (payload.get("instrument_type") or payload.get("instrument_token")):
        payment_instrument = {
            "instrument_type": payload.get("instrument_type"),
            "instrument_id": payload.get("instrument_token"),
        }

    return {
        "player_id": (
            payload.get("player_id")
            or payload.get("playerId")
            or payload.get("user_id")
            or payload.get("external_player_id")
            or payload.get("player_cpf")
        ),
        "amount": amount,
        "type": str(tx_type).upper(),
        "method": method or "OTHER",
        "status": str(status).upper(),
        "currency": payload.get("currency") or payload.get("ccy") or "BRL",
        "occurred_at": occurred_at,
        "payment_instrument": payment_instrument,
    }


def _normalize_bet_payload(payload: dict) -> dict:
    stake_amount = _coerce_float(payload.get("stake_amount") or payload.get("stakeAmount") or payload.get("amount"), 0.0)
    odds = payload.get("odds")
    potential_payout = payload.get("potential_payout") or payload.get("potentialPayout")
    settled_payout = payload.get("settled_payout") or payload.get("actual_payout")
    placed_at = payload.get("placed_at") or payload.get("occurred_at") or payload.get("timestamp") or _iso_now()

    return {
        "player_id": (
            payload.get("player_id")
            or payload.get("playerId")
            or payload.get("user_id")
            or payload.get("external_player_id")
            or payload.get("player_cpf")
        ),
        "stake_amount": stake_amount,
        "odds": _coerce_float(odds, 0.0) if odds is not None else None,
        "potential_payout": _coerce_float(potential_payout, 0.0) if potential_payout is not None else None,
        "settled_payout": _coerce_float(settled_payout, 0.0) if settled_payout is not None else None,
        "market_type": payload.get("market_type") or payload.get("market"),
        "sport": payload.get("sport"),
        "channel": payload.get("channel") or "WEB",
        "placed_at": placed_at,
        "status": payload.get("status") or "OPEN",
        "outcome": payload.get("outcome"),
    }


def _normalize_device_payload(payload: dict) -> dict:
    occurred_at = payload.get("occurred_at") or payload.get("timestamp") or _iso_now()
    return {
        "player_id": (
            payload.get("player_id")
            or payload.get("playerId")
            or payload.get("user_id")
            or payload.get("external_player_id")
            or payload.get("player_cpf")
        ),
        "device_id": payload.get("device_id") or payload.get("deviceId") or payload.get("fingerprint"),
        "action": payload.get("action") or payload.get("event_type") or "LOGIN",
        "ip": payload.get("ip") or payload.get("ip_address"),
        "country_code": payload.get("country_code") or payload.get("geo_country"),
        "user_agent": payload.get("user_agent"),
        "occurred_at": occurred_at,
    }


async def process_raw_transaction(msg_value: dict, producer) -> None:
    payload = msg_value.get("payload", {})
    normalized = _normalize_transaction_payload(payload)
    if not msg_value.get("tenant_id") or not normalized.get("player_id"):
        return

    canonical = {
        "event_id": msg_value.get("event_id") or str(uuid.uuid4()),
        "tenant_id": msg_value.get("tenant_id"),
        "source_system": msg_value.get("source_system", "unknown"),
        "source_event_id": msg_value.get("source_event_id") or msg_value.get("event_id") or str(uuid.uuid4()),
        "schema_version": 1,
        "entity_type": "TRANSACTION",
        "occurred_at": normalized["occurred_at"],
        "payload": normalized,
        "raw_payload": msg_value.get("raw_payload") or payload,
        "ingest_metadata": msg_value.get("ingest_metadata") or {},
    }
    await producer.send("canonical.transactions", canonical, key=str(canonical["source_event_id"]))


async def process_raw_bet(msg_value: dict, producer) -> None:
    payload = msg_value.get("payload", {})
    normalized = _normalize_bet_payload(payload)
    if not msg_value.get("tenant_id") or not normalized.get("player_id"):
        return

    canonical = {
        "event_id": msg_value.get("event_id") or str(uuid.uuid4()),
        "tenant_id": msg_value.get("tenant_id"),
        "source_system": msg_value.get("source_system", "unknown"),
        "source_event_id": msg_value.get("source_event_id") or msg_value.get("event_id") or str(uuid.uuid4()),
        "schema_version": 1,
        "entity_type": "BET",
        "occurred_at": normalized["placed_at"],
        "payload": normalized,
        "raw_payload": msg_value.get("raw_payload") or payload,
        "ingest_metadata": msg_value.get("ingest_metadata") or {},
    }
    await producer.send("canonical.bets", canonical, key=str(canonical["source_event_id"]))


async def process_raw_device_event(msg_value: dict, producer) -> None:
    payload = msg_value.get("payload", {})
    normalized = _normalize_device_payload(payload)
    if not msg_value.get("tenant_id") or not normalized.get("device_id"):
        return

    canonical = {
        "event_id": msg_value.get("event_id") or str(uuid.uuid4()),
        "tenant_id": msg_value.get("tenant_id"),
        "source_system": msg_value.get("source_system", "unknown"),
        "source_event_id": msg_value.get("source_event_id") or msg_value.get("event_id") or str(uuid.uuid4()),
        "schema_version": 1,
        "entity_type": "DEVICE_EVENT",
        "occurred_at": normalized["occurred_at"],
        "payload": normalized,
        "raw_payload": msg_value.get("raw_payload") or payload,
        "ingest_metadata": msg_value.get("ingest_metadata") or {},
    }
    await producer.send("canonical.device_events", canonical, key=str(canonical["source_event_id"]))

# ──────────────────────────────────────────────────
# Redis Sorted Set helpers para janelas de tempo
# Key: betaml:{tenant_id}:txn:{player_id}
# Score: timestamp Unix epoch (float)
# Value: JSON da entrada
# TTL: 90 dias (7 776 000 s)
# ──────────────────────────────────────────────────

WINDOW_TTL_SECONDS = 90 * 24 * 3600  # 90 dias
FEATURE_STORE_TTL_SECONDS = 4 * 3600


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
            entry["ts"] = _to_naive_utc_datetime(ts_raw)
            result.append(entry)
        except Exception:
            pass
    return result


def _latest_feature_snapshot_rows_sync() -> list[dict]:
    import sqlalchemy as sa

    engine = _get_oltp_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                """
                SELECT DISTINCT ON (tenant_id, player_id)
                       tenant_id, player_id, feature_date, features, created_at
                FROM feature_snapshots
                ORDER BY tenant_id, player_id, feature_date DESC, created_at DESC
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def _build_warmed_feature_store_entry(row: dict) -> tuple[str, dict[str, str]] | None:
    tenant_id = str(row.get("tenant_id") or "")
    player_id = str(row.get("player_id") or "")
    if not tenant_id or not player_id:
        return None

    raw_features = row.get("features") or {}
    if isinstance(raw_features, str):
        try:
            raw_features = json.loads(raw_features)
        except Exception:
            raw_features = {}
    if not isinstance(raw_features, dict) or not raw_features:
        return None

    feature_date = row.get("feature_date")
    feature_date_str = (
        feature_date.isoformat()
        if hasattr(feature_date, "isoformat")
        else str(feature_date or _now_naive_utc().date().isoformat())
    )
    feature_version = raw_features.get("feature_version", 2)
    try:
        feature_version = int(feature_version or 2)
    except (TypeError, ValueError):
        feature_version = 2

    snapshot_version = raw_features.get("snapshot_version", feature_version)
    try:
        snapshot_version = int(snapshot_version or feature_version)
    except (TypeError, ValueError):
        snapshot_version = feature_version

    features = dict(raw_features)
    features.setdefault("tenant_id", tenant_id)
    features.setdefault("player_id", player_id)
    features.setdefault("snapshot_date", feature_date_str)
    features.setdefault("entity_type", "PLAYER")
    features.setdefault("feature_version", feature_version)
    features.setdefault("snapshot_version", snapshot_version)
    features.setdefault(
        "gold_object_path",
        (
            f"gold/tenant_id={tenant_id}/feature_date={feature_date_str}/"
            f"entity_type=PLAYER/player_id={player_id}.json"
        ),
    )
    features["warmed_from"] = "feature_snapshot"
    redis_key = f"betaml:{tenant_id}:features:{player_id}"
    return redis_key, {key: str(value) for key, value in features.items()}


async def warm_feature_store_cache(redis_client) -> int:
    """Restaura no Redis o snapshot Gold mais recente por jogador."""
    warmed = 0
    try:
        rows = await asyncio.to_thread(_latest_feature_snapshot_rows_sync)
        for row in rows:
            warmed_entry = _build_warmed_feature_store_entry(row)
            if warmed_entry is None:
                continue
            redis_key, mapping = warmed_entry
            await redis_client.hset_dict(redis_key, mapping, ttl=FEATURE_STORE_TTL_SECONDS)
            warmed += 1
        logger.info("stream_feature_store_cache_warmed", players=warmed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stream_feature_store_cache_warm_failed", error=str(exc))
    return warmed


async def compute_features(
    tenant_id: str,
    player_id: str,
    redis_client,
    ch_client,
    *,
    current_event: dict | None = None,
) -> dict:
    now = _now_naive_utc()
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
    bets_90d        = [b for b in bets if b["ts"] >= cutoff_90d]
    deposits_1h     = filter_type(txns, "DEPOSIT", cutoff_1h)

    dep_sum_24h  = sum(e["amount"] for e in deposits_24h)
    dep_sum_7d   = sum(e["amount"] for e in deposits_7d)
    dep_sum_30d  = sum(e["amount"] for e in deposits_30d)
    dep_sum_90d  = sum(e["amount"] for e in deposits_90d)
    with_sum_24h = sum(e["amount"] for e in withdrawal_24h)
    with_sum_7d  = sum(e["amount"] for e in withdrawal_7d)
    with_sum_90d = sum(e["amount"] for e in withdrawal_90d)
    bet_sum_24h  = sum(b["amount"] for b in bets_24h)
    bet_sum_7d   = sum(b["amount"] for b in bets_7d)

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

    # 11. Cashout ratio (7d) = apostas com cashout / apostas totais
    cashout_bets_7d = [
        b
        for b in bets_7d
        if b.get("cashout_amount") not in (None, "", 0, 0.0)
        or str(b.get("status") or "").upper() in {"CASHOUT", "CASHED_OUT", "EARLY_CASHOUT"}
    ]
    cashout_ratio_7d = len(cashout_bets_7d) / max(len(bets_7d), 1)

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

    gold_object_path = (
        f"gold/tenant_id={tenant_id}/feature_date={now.date().isoformat()}/"
        f"entity_type=PLAYER/player_id={player_id}.json"
    )

    features = {
        "player_id":     player_id,
        "tenant_id":     tenant_id,
        "computed_at":   now.isoformat(),
        "feature_version": 2,           # bumped for M2
        "snapshot_version": 2,
        "entity_type": "PLAYER",
        "snapshot_date": now.date().isoformat(),
        "gold_object_path": gold_object_path,

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
        "new_payment_instrument_flag":          bool(current_event.get("is_new_instrument", False)) if current_event else False,
        "new_device_flag":                      bool(current_event.get("is_new_device", False)) if current_event else False,
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
        "inconsistent_currency_flag":                  multi_currency,
        "chargeback_rate_30d":                  float(chargeback_rate_30d),
        "bonus_to_real_ratio_30d":              float(bonus_ratio_30d),
        "bonus_to_real_money_ratio_30d":        float(bonus_ratio_30d),
        "cashout_ratio_7d":                     float(cashout_ratio_7d),
        "bet_count_7d":                         len(bets_7d),
        "bet_count_30d":                        len(bets_30d),
        "bet_count_90d":                        len(bets_90d),

        # network
        "shared_device_score":                  float(shared_device_score),
        "shared_instrument_score":              float(shared_instrument_score),
        "cluster_id":                           cluster_id,
        "cluster_size":                         int(cluster_size),
    }

    # Persist to Redis (online store, TTL 4h)
    redis_key = f"betaml:{tenant_id}:features:{player_id}"
    await redis_client.hset_dict(
        redis_key,
        {k: str(v) for k, v in features.items()},
        ttl=FEATURE_STORE_TTL_SECONDS,
    )

    # Persist to ClickHouse (Gold — async via thread)
    try:
        await asyncio.to_thread(_ch_insert_features, ch_client, features, now.date())
    except Exception as e:
        logger.warning("ch_insert_features_failed", error=str(e))

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
        "multi_currency_flag":        int(bool(features.get("inconsistent_currency_flag", False))),
        "chargeback_rate_30d":        _f("chargeback_rate_30d"),
        "bonus_to_real_ratio_30d":    _f("bonus_to_real_ratio_30d"),
        "cashout_ratio_7d":           _f("cashout_ratio_7d"),
        "shared_instrument_score":    _f("shared_instrument_score"),
        "feature_version":            int(features.get("feature_version", 2)),
        "computed_at":                _now_naive_utc(),
    }
    ch_client.insert_dict("betaml.player_features_daily", [row])


def _persist_feature_snapshot(features: dict, feature_date) -> None:
    import io
    import sqlalchemy as sa

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = sa.create_engine(sync_url, pool_pre_ping=True)
    payload = json.dumps(features, ensure_ascii=False, default=str)
    player_id = features.get("player_id")
    tenant_id = features.get("tenant_id")

    resolved_player_id = player_id
    if not _is_uuid(resolved_player_id) and _is_uuid(tenant_id):
        try:
            with engine.connect() as conn:
                mapped = conn.execute(
                    sa.text(
                        """
                        SELECT id
                        FROM players
                        WHERE tenant_id = :tenant_id
                          AND (
                            external_player_id = :external_player_id
                            OR external_id = :external_player_id
                          )
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "external_player_id": str(player_id),
                    },
                ).scalar_one_or_none()
            if mapped:
                resolved_player_id = str(mapped)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feature_snapshot_player_resolve_failed",
                error=str(exc),
                tenant_id=tenant_id,
                player_id=player_id,
            )

    try:
        if _is_uuid(resolved_player_id) and _is_uuid(tenant_id):
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
                        "id": str(uuid.uuid4()),
                        "tenant_id": tenant_id,
                        "player_id": resolved_player_id,
                        "feature_date": feature_date,
                        "snapshot_date": feature_date,
                        "features": payload,
                    },
                )
        else:
            logger.info(
                "feature_snapshot_skipped_non_uuid_player",
                tenant_id=tenant_id,
                player_id=player_id,
                resolved_player_id=resolved_player_id,
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
        object_name = str(
            features.get("gold_object_path")
            or f"gold/tenant_id={features['tenant_id']}/feature_date={feature_date.isoformat()}/entity_type=PLAYER/player_id={features['player_id']}.json"
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
        occurred_at = _to_naive_utc_datetime(payload.get("occurred_at", _iso_now()))
    except (ValueError, TypeError):
        occurred_at = _now_naive_utc()

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

    # Determine if this payment instrument is new for this player (before recording it)
    instr_method = payload.get("method", "")
    instr_parts = [instr_method]
    if isinstance(instrument, dict):
        for k in sorted(instrument.keys()):
            v = instrument[k]
            if v is not None:
                instr_parts.append(f"{k}={v}")
    instr_fingerprint = hashlib.sha256("|".join(instr_parts).encode()).hexdigest()[:24]
    pinstr_key = redis_client.player_instruments_key(tenant_id, player_id)
    is_new_instrument = not await redis_client.sismember(pinstr_key, instr_fingerprint)
    await redis_client.sadd_member(pinstr_key, instr_fingerprint)

    # Compute + store features
    features = await compute_features(
        tenant_id, player_id, redis_client, ch_client,
        current_event={"is_new_instrument": is_new_instrument},
    )

    # GAP-stream: backfill não dispara features em tempo real (evita sobrecarga de writes
    # durante importação histórica em massa e não contamina baseline incremental).
    _ingest_mode = (msg_value.get("ingest_metadata") or {}).get("ingest_mode") or msg_value.get("ingest_mode") or "incremental"
    if _ingest_mode != "backfill":
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

    try:
        await asyncio.to_thread(_persist_transaction_oltp, msg_value, payload)
    except Exception as e:
        logger.warning("oltp_insert_transaction_failed", error=str(e))


def _ch_insert_transaction(ch_client, envelope: dict, payload: dict) -> None:
    try:
        occurred_at = _to_naive_utc_datetime(payload.get("occurred_at", _iso_now()))
    except Exception:
        occurred_at = _now_naive_utc()
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
        "created_at":       _now_naive_utc(),
    }
    ch_client.insert_dict("betaml.transactions", [row])


async def process_bet(msg_value: dict, redis_client, ch_client, producer):
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    try:
        placed_at = _to_naive_utc_datetime(payload.get("placed_at", _iso_now()))
    except Exception:
        placed_at = _now_naive_utc()

    entry = {
        "ts":      placed_at.isoformat(),
        "amount":  float(payload.get("stake_amount", 0)),
        "odds":    payload.get("odds"),
        "outcome": payload.get("outcome"),
        "status": payload.get("status"),
        "settled_payout": payload.get("settled_payout"),
        "cashout_amount": payload.get("cashout_amount") or payload.get("cashoutValue"),
    }
    bet_key = redis_client.bet_window_key(tenant_id, player_id)
    await _zadd_entry(redis_client, bet_key, placed_at, entry)

    await compute_features(tenant_id, player_id, redis_client, ch_client)

    try:
        await asyncio.to_thread(_ch_insert_bet, ch_client, msg_value, payload)
    except Exception as e:
        logger.warning("ch_insert_bet_failed", error=str(e))

    try:
        await asyncio.to_thread(_persist_bet_oltp, msg_value, payload)
    except Exception as e:
        logger.warning("oltp_insert_bet_failed", error=str(e))


def _ch_insert_bet(ch_client, envelope: dict, payload: dict) -> None:
    try:
        placed_at = _to_naive_utc_datetime(payload.get("placed_at", _iso_now()))
    except Exception:
        placed_at = _now_naive_utc()
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
        "created_at":     _now_naive_utc(),
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
        # Check if this device is new for the player BEFORE recording it
        is_new_device = not await redis_client.sismember(pdev_key, device_id)
        await redis_client.sadd_member(dev_key, player_id)
        await redis_client.sadd_member(pdev_key, device_id)
        # Recompute features, passing the new-device signal
        await compute_features(
            tenant_id, player_id, redis_client, ch_client,
            current_event={"is_new_device": is_new_device},
        )

    try:
        await asyncio.to_thread(_persist_device_event_oltp, msg_value, payload)
    except Exception as e:
        logger.warning("oltp_insert_device_event_failed", error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# GAP-stream: handlers para entidades de ciclo de vida do player
# KYC_EVENT | RESPONSIBLE_GAMBLING_EVENT | ACCOUNT_STATUS_CHANGE
# ─────────────────────────────────────────────────────────────────────────────

async def process_kyc_event(msg_value: dict, redis_client, ch_client, producer) -> None:
    """Processa canonical.kyc_events → ClickHouse + OLTP."""
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    try:
        await asyncio.to_thread(_ch_insert_kyc_event, ch_client, msg_value, payload)
    except Exception as exc:
        logger.warning("ch_insert_kyc_event_failed", error=str(exc))

    try:
        await asyncio.to_thread(_persist_kyc_event_oltp, msg_value, payload)
    except Exception as exc:
        logger.warning("oltp_persist_kyc_event_failed", error=str(exc))


async def process_responsible_gambling_event(msg_value: dict, redis_client, ch_client, producer) -> None:
    """Processa canonical.responsible_gambling_events → ClickHouse + OLTP.

    Auto-exclusão SIGAP/SPA (Portaria 1.143/2024 art. 9°): subtype
    SELF_EXCLUSION_SIGAP ou SELF_EXCLUSION_OPERATOR → SUSPENDED no Postgres.
    """
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    try:
        await asyncio.to_thread(_ch_insert_kyc_event, ch_client, msg_value, payload)
    except Exception as exc:
        logger.warning("ch_insert_rg_event_failed", error=str(exc))

    subtype = str(payload.get("subtype", "")).upper()
    if subtype in {"SELF_EXCLUSION_SIGAP", "SELF_EXCLUSION_OPERATOR", "COOLING_OFF"}:
        try:
            await asyncio.to_thread(_suspend_player_oltp, tenant_id, player_id, subtype)
        except Exception as exc:
            logger.warning("oltp_suspend_player_failed", player_id=player_id, error=str(exc))

    try:
        await asyncio.to_thread(_persist_kyc_event_oltp, msg_value, payload)
    except Exception as exc:
        logger.warning("oltp_persist_rg_event_failed", error=str(exc))


async def process_account_status_change(msg_value: dict, redis_client, ch_client, producer) -> None:
    """Processa canonical.account_status_changes → ClickHouse + OLTP (atualiza players.status)."""
    tenant_id = msg_value.get("tenant_id")
    payload   = msg_value.get("payload", {})
    player_id = payload.get("player_id") or payload.get("playerId")
    if not tenant_id or not player_id:
        return

    try:
        await asyncio.to_thread(_ch_insert_kyc_event, ch_client, msg_value, payload)
    except Exception as exc:
        logger.warning("ch_insert_account_status_failed", error=str(exc))

    new_status = payload.get("new_status")
    if new_status:
        try:
            await asyncio.to_thread(_update_player_status_oltp, tenant_id, player_id, new_status)
        except Exception as exc:
            logger.warning("oltp_update_player_status_failed", player_id=player_id, error=str(exc))

    try:
        await asyncio.to_thread(_persist_kyc_event_oltp, msg_value, payload)
    except Exception as exc:
        logger.warning("oltp_persist_account_status_failed", error=str(exc))


# ────────────────────────────────────────────────────────────
# ClickHouse helper — betaml.player_kyc_events
# Cobre KYC_EVENT, RESPONSIBLE_GAMBLING_EVENT, ACCOUNT_STATUS_CHANGE
# ────────────────────────────────────────────────────────────

def _ch_insert_kyc_event(ch_client, envelope: dict, payload: dict) -> None:
    try:
        occurred_at = _to_naive_utc_datetime(payload.get("occurred_at", _iso_now()))
    except Exception:
        occurred_at = _now_naive_utc()

    _ingest_meta = envelope.get("ingest_metadata") or {}
    row = {
        "event_id":        envelope.get("event_id", str(uuid.uuid4())),
        "tenant_id":       envelope.get("tenant_id", ""),
        "source_system":   envelope.get("source_system", ""),
        "player_id":       payload.get("player_id", ""),
        "entity_type":     str(envelope.get("entity_type", "")).upper(),
        "subtype":         str(payload.get("subtype", "")).upper(),
        "provider":        payload.get("provider", ""),
        "document_type":   payload.get("document_type", ""),
        "pep_flag":        bool(payload.get("pep_flag", False)),
        "income_declared": float(payload.get("income_declared") or 0),
        "exclusion_source":         payload.get("exclusion_source", ""),
        "exclusion_scope":          payload.get("exclusion_scope", ""),
        "exclusion_duration_days":  int(payload.get("exclusion_duration_days") or 0),
        "old_deposit_limit":        float(payload.get("old_deposit_limit") or 0),
        "new_deposit_limit":        float(payload.get("new_deposit_limit") or 0),
        "previous_status":          payload.get("previous_status", ""),
        "new_status":               payload.get("new_status", ""),
        "reason":                   payload.get("reason", ""),
        "ingest_mode":    str(_ingest_meta.get("ingest_mode") or envelope.get("ingest_mode") or "incremental"),
        "backfill_job_id": str(_ingest_meta.get("backfill_job_id") or envelope.get("backfill_job_id") or ""),
        "occurred_at": occurred_at,
        "event_date":  occurred_at.date(),
        "created_at":  _now_naive_utc(),
    }
    ch_client.insert_dict("betaml.player_kyc_events", [row])


# ────────────────────────────────────────────────────────────
# OLTP helpers — KYC/RG/AccountStatus
# ────────────────────────────────────────────────────────────

def _persist_kyc_event_oltp(envelope: dict, payload: dict) -> None:
    """Persiste evento KYC/RG/AccountStatus na tabela player_kyc_events do Postgres."""
    import sqlalchemy as sa

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    tenant_id = envelope.get("tenant_id", "")

    try:
        occurred_at = _to_naive_utc_datetime(payload.get("occurred_at", _iso_now()))
    except Exception:
        occurred_at = _now_naive_utc()

    _ingest_meta = envelope.get("ingest_metadata") or {}
    engine = sa.create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                sa.text(
                    """
                    INSERT INTO player_kyc_events (
                        id, tenant_id, player_id, entity_type, subtype,
                        provider, document_type, pep_flag, income_declared,
                        exclusion_source, exclusion_scope, exclusion_duration_days,
                        old_deposit_limit, new_deposit_limit,
                        previous_status, new_status, reason,
                        ingest_mode, backfill_job_id,
                        occurred_at, created_at
                    ) VALUES (
                        :id, :tenant_id, :player_id, :entity_type, :subtype,
                        :provider, :document_type, :pep_flag, :income_declared,
                        :exclusion_source, :exclusion_scope, :exclusion_duration_days,
                        :old_deposit_limit, :new_deposit_limit,
                        :previous_status, :new_status, :reason,
                        :ingest_mode, :backfill_job_id,
                        :occurred_at, NOW()
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": envelope.get("event_id") or str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "player_id": payload.get("player_id", ""),
                    "entity_type": str(envelope.get("entity_type", "")).upper(),
                    "subtype": str(payload.get("subtype", "")).upper(),
                    "provider": payload.get("provider") or "",
                    "document_type": payload.get("document_type") or "",
                    "pep_flag": bool(payload.get("pep_flag", False)),
                    "income_declared": float(payload.get("income_declared") or 0),
                    "exclusion_source": payload.get("exclusion_source") or "",
                    "exclusion_scope": payload.get("exclusion_scope") or "",
                    "exclusion_duration_days": int(payload.get("exclusion_duration_days") or 0),
                    "old_deposit_limit": float(payload.get("old_deposit_limit") or 0),
                    "new_deposit_limit": float(payload.get("new_deposit_limit") or 0),
                    "previous_status": payload.get("previous_status") or "",
                    "new_status": payload.get("new_status") or "",
                    "reason": payload.get("reason") or "",
                    "ingest_mode": str(_ingest_meta.get("ingest_mode") or envelope.get("ingest_mode") or "incremental"),
                    "backfill_job_id": str(_ingest_meta.get("backfill_job_id") or envelope.get("backfill_job_id") or ""),
                    "occurred_at": occurred_at,
                },
            )
    finally:
        engine.dispose()


def _suspend_player_oltp(tenant_id: str, player_id: str, reason: str) -> None:
    """Marca player como SUSPENDED (auto-exclusão — Lei 14.790/2023 art. 13)."""
    import sqlalchemy as sa

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = sa.create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE players
                    SET status = 'SUSPENDED',
                        updated_at = NOW()
                    WHERE id = :player_id
                      AND tenant_id = :tenant_id
                      AND status NOT IN ('SUSPENDED', 'CLOSED_BY_OPERATOR')
                    """
                ),
                {"player_id": player_id, "tenant_id": tenant_id},
            )
    finally:
        engine.dispose()
    logger.info("player_suspended_auto_exclusion", tenant_id=tenant_id, player_id=player_id, reason=reason)


def _update_player_status_oltp(tenant_id: str, player_id: str, new_status: str) -> None:
    """Atualiza players.status a partir de AccountStatusChange canônico."""
    import sqlalchemy as sa

    _ALLOWED = {
        "ACTIVE", "BLOCKED_BY_OPERATOR", "SUSPENDED", "REACTIVATED",
        "CLOSED_BY_PLAYER", "CLOSED_BY_OPERATOR",
    }
    if new_status.upper() not in _ALLOWED:
        return

    db_url = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    engine = sa.create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                sa.text(
                    """
                    UPDATE players
                    SET status = :new_status,
                        updated_at = NOW()
                    WHERE id = :player_id
                      AND tenant_id = :tenant_id
                    """
                ),
                {"player_id": player_id, "tenant_id": tenant_id, "new_status": new_status.upper()},
            )
    finally:
        engine.dispose()


async def process_ingest_job(msg_value: dict, redis_client, ch_client, producer) -> None:
    """
    Consome mensagem de ingest.jobs:
            - Carrega arquivo no Data Lake (MinIO) por file_path
      - Aplica MappingConfig (via MappingEngine) se mapping_config_id fornecido
      - Publica eventos em raw.{entity_type}s
      - Atualiza IngestJob no Postgres com DONE/FAILED + contagens
    """
    import csv
    import io
    import sqlalchemy as sa
    from minio import Minio

    from libs.connectors import get_connector
    from libs.mapping import MappingEngine, get_default_mapping

    job_id = msg_value.get("job_id")
    tenant_id = msg_value.get("tenant_id")
    source_system = msg_value.get("source_system", "")
    mapping_config_id = msg_value.get("mapping_version_id") or msg_value.get("mapping_config_id")
    file_name = msg_value.get("file_name", "")
    file_path = msg_value.get("file_path")
    # GAP-stream: propagar ingest_mode para todos os envelopes publicados
    ingest_mode = str(msg_value.get("ingest_mode") or "incremental")
    backfill_job_id = msg_value.get("backfill_job_id")

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
            # RLS: ensure UPDATE is visible for this tenant.
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
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

    def _insert_ingest_error(*, raw_payload: dict, line_number: int, reason: str, entity_type: str = "TRANSACTION"):
        engine = sa.create_engine(sync_url, pool_pre_ping=True)
        with engine.begin() as conn:
            # RLS: ensure INSERT is allowed/visible for this tenant.
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": str(tenant_id)},
            )
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
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "ingest_job_id": job_id,
                    "source_system": source_system,
                    "entity_type": entity_type,
                    "raw_payload": json.dumps(raw_payload, ensure_ascii=False),
                    "error_reason": reason,
                    "error_detail": json.dumps({"line_number": line_number}, ensure_ascii=False),
                    "line_number": line_number,
                },
            )
        engine.dispose()

    stream: io.TextIOWrapper | None = None
    obj = None
    try:
        if not file_path:
            raise ValueError("missing file_path")

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
        # Evita problemas de stream fechado (urllib3/HTTPResponse) lendo em memória.
        raw_bytes = obj.read()  # type: ignore[attr-defined]
        text = raw_bytes.decode("utf-8", errors="replace")
        stream = io.StringIO(text)
    except Exception as exc:
        await asyncio.to_thread(_update_job, "FAILED", 0, 0, 0, f"file load error: {exc}")
        return

    def _resolve_mapping_cfg() -> dict | None:
        engine = sa.create_engine(sync_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                if mapping_config_id:
                    row = conn.execute(
                        sa.text("SELECT config_json FROM mapping_configs WHERE id = :id"),
                        {"id": mapping_config_id},
                    ).fetchone()
                    if row:
                        return dict(row._mapping)["config_json"]

                row = conn.execute(
                    sa.text(
                        """
                        SELECT config_json
                        FROM mapping_configs
                        WHERE tenant_id = :tenant_id
                          AND source_system = :source_system
                          AND entity_type = 'TRANSACTION'
                          AND is_current = true
                          AND active = true
                        ORDER BY version_number DESC
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": tenant_id, "source_system": source_system},
                ).fetchone()
                if row:
                    return dict(row._mapping)["config_json"]
        except Exception:
            pass
        finally:
            engine.dispose()

        default_cfg = get_default_mapping(str(source_system), "TRANSACTION")
        return dict(default_cfg) if isinstance(default_cfg, dict) else None

    mapping_cfg = _resolve_mapping_cfg()

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
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                            "target_topic": topic,
                            "raw_payload": row_data,
                        },
                        key=str(job_id),
                    )
                    return False
                await asyncio.sleep(0.1 * attempt)
        return False

    mapper: MappingEngine | None = None
    if isinstance(mapping_cfg, dict):
        try:
            mapper = MappingEngine(mapping_cfg)
        except Exception as exc:
            logger.warning("ingest_job_invalid_mapping", job_id=job_id, error=str(exc))

    connector_source_map = {
        "ConnectorGamma": ("gamma", {"root_tag": "transaction"}),
        "ConnectorDelta": ("delta", {}),
        "ConnectorEpsilon": ("epsilon", {}),
    }
    connector_conf = connector_source_map.get(str(source_system))
    if connector_conf:
        connector_name, connector_kwargs = connector_conf
        connector = get_connector(connector_name, **connector_kwargs)
        parse_result = connector.parse(raw_bytes, entity_type="TRANSACTION")

        total = int(parse_result.total or 0)
        processed = 0
        failed = 0
        bytes_processed = len(raw_bytes)
        error_sample: list[dict[str, object]] = []
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await asyncio.to_thread(_update_job, "PROCESSING", total, 0, 0)

        for idx, rec in enumerate(parse_result.records, start=1):
            try:
                mapped_rec = mapper.apply(rec) if mapper else rec
                entity_type = str(mapped_rec.get("entity_type") or "transaction").lower()
                source_event_id = str(
                    mapped_rec.get("event_id")
                    or rec.get("event_id")
                    or mapped_rec.get("external_id")
                    or rec.get("external_id")
                    or uuid.uuid4()
                )
                envelope = {
                    "event_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "source_system": source_system,
                    "source_event_id": source_event_id,
                    "schema_version": 1,
                    "entity_type": entity_type,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "payload": mapped_rec,
                    "raw_payload": rec,
                    "mapping_config_id": mapping_config_id,
                    "ingest_metadata": {
                        "job_id": job_id,
                        "source": "file",
                        "channel": "connector-reprocess",
                        "ingest_mode": ingest_mode,
                        "backfill_job_id": backfill_job_id,
                    },
                }
                topic = f"canonical.{entity_type}s"
                ok = await _publish_with_retries(
                    topic,
                    envelope,
                    envelope["source_event_id"] or envelope["event_id"],
                    rec,
                    idx,
                )
                if ok:
                    processed += 1
                else:
                    failed += 1
                    if len(error_sample) < 10:
                        error_sample.append({"line": idx, "reason": "publish_failed_after_retries", "raw": rec})
                    await asyncio.to_thread(
                        _insert_ingest_error,
                        raw_payload=rec,
                        line_number=idx,
                        reason="publish_failed_after_retries",
                    )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                reason = str(exc)
                if len(error_sample) < 10:
                    error_sample.append({"line": idx, "reason": reason, "raw": rec})
                await asyncio.to_thread(
                    _insert_ingest_error,
                    raw_payload=rec,
                    line_number=idx,
                    reason=reason,
                )

        for err in parse_result.errors:
            failed += 1
            reason = err.get("reason", "parse_error") if isinstance(err, dict) else str(err)
            raw_payload = err.get("raw", "") if isinstance(err, dict) else ""
            line_number = err.get("line") if isinstance(err, dict) else None
            if len(error_sample) < 10:
                error_sample.append({"line": line_number, "reason": reason, "raw": raw_payload})
            await asyncio.to_thread(
                _insert_ingest_error,
                raw_payload=raw_payload,
                line_number=int(line_number or 0),
                reason=reason,
            )

        final_status = "DONE" if failed == 0 else ("PARTIAL" if processed > 0 else "FAILED")
        duration_ms = int((datetime.now(timezone.utc).replace(tzinfo=None) - started_at).total_seconds() * 1000)
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
            connector=connector_name,
        )
        return

    def _iter_json_lines(text_stream: io.TextIOBase):
        for raw_line in text_stream:
            line = raw_line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    yield parsed
            except Exception:
                continue

    normalized_file_name = str(file_name or "").lower()
    if normalized_file_name.endswith((".jsonl", ".ndjson", ".json")):
        row_iter = _iter_json_lines(stream)
    else:
        row_iter = csv.DictReader(stream)

    total = 0
    processed = 0
    failed = 0
    bytes_processed = 0
    error_sample: list[dict[str, object]] = []
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await asyncio.to_thread(_update_job, "PROCESSING", total, 0, 0)

    try:
        for idx, row_data in enumerate(row_iter, start=1):
            total += 1
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
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "payload": payload,
                    "raw_payload": row_data,
                    "ingest_metadata": {
                        "job_id": job_id,
                        "source": "file",
                        "ingest_mode": ingest_mode,
                        "backfill_job_id": backfill_job_id,
                    },
                }
                topic = f"canonical.{entity_type}s"
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
                    error_entity_type = str(
                        row_data.get("entity_type")
                        or (mapping_cfg.get("entity_type", "transaction") if mapping_cfg else "transaction")
                    ).upper()
                    if len(error_sample) < 10:
                        error_sample.append({"line": idx, "reason": "publish_failed_after_retries", "raw": row_data})
                    await asyncio.to_thread(
                        _insert_ingest_error,
                        raw_payload=row_data,
                        line_number=idx,
                        reason="publish_failed_after_retries",
                        entity_type=error_entity_type,
                    )
            except Exception as exc:
                reason = str(exc)
                logger.warning("ingest_row_failed", job_id=job_id, line=idx, error=reason)
                failed += 1
                error_entity_type = str(
                    row_data.get("entity_type")
                    or (mapping_cfg.get("entity_type", "transaction") if mapping_cfg else "transaction")
                ).upper()
                target_topic = f"canonical.{error_entity_type.lower()}s"
                if len(error_sample) < 10:
                    error_sample.append({"line": idx, "reason": reason, "raw": row_data})

                await asyncio.to_thread(
                    _insert_ingest_error,
                    raw_payload=row_data,
                    line_number=idx,
                    reason=reason,
                    entity_type=error_entity_type,
                )

                try:
                    await producer.send(
                        f"{target_topic}.dlq",
                        {
                            "tenant_id": tenant_id,
                            "job_id": job_id,
                            "source_system": source_system,
                            "line_number": idx,
                            "reason": reason,
                            "attempt": max_retries,
                            "max_retries": max_retries,
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                            "target_topic": target_topic,
                            "raw_payload": row_data,
                        },
                        key=str(job_id),
                    )
                except Exception as dlq_exc:
                    logger.warning("dlq_publish_failed", job_id=job_id, error=str(dlq_exc))
    except Exception as stream_exc:
        failed += 1
        reason = f"stream_parse_error: {stream_exc}"
        logger.warning("ingest_stream_failed", job_id=job_id, error=str(stream_exc))
        if len(error_sample) < 10:
            error_sample.append({"line": total + 1, "reason": reason, "raw": {}})
    finally:
        if stream is not None:
            stream.close()
        if obj is not None:
            obj.close()
            obj.release_conn()

    final_status = "DONE" if failed == 0 else ("PARTIAL" if processed > 0 else "FAILED")
    duration_ms = int((datetime.now(timezone.utc).replace(tzinfo=None) - started_at).total_seconds() * 1000)
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

    start_http_server(METRICS_PORT)
    init_opentelemetry_stub("stream-processor")

    redis_client = RedisClient(REDIS_URL)
    await redis_client.connect()

    ch_client = ClickHouseClient(host=CH_HOST, port=CH_PORT, database=CH_DB)
    ch_client.connect()

    await warm_feature_store_cache(redis_client)

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
            started = time.monotonic()
            topic = getattr(msg, "topic", "unknown")
            try:
                value = msg.value if isinstance(msg.value, dict) else json.loads(msg.value)
                structlog.contextvars.bind_contextvars(
                    event_id=str(value.get("event_id") or value.get("source_event_id") or ""),
                    tenant_id=str(value.get("tenant_id") or ""),
                )
                highwater = getattr(msg, "highwater", None)
                offset = getattr(msg, "offset", None)
                if isinstance(highwater, int) and isinstance(offset, int):
                    CONSUMER_LAG.labels(group_id="stream-processor", topic=topic).set(max(highwater - offset - 1, 0))

                if topic == "canonical.transactions":
                    await process_transaction(value, redis_client, ch_client, producer)
                elif topic == "canonical.bets":
                    await process_bet(value, redis_client, ch_client, producer)
                elif topic == "canonical.device_events":
                    await process_device_event(value, redis_client, ch_client, producer)
                # GAP-stream: novos tópicos de ciclo de vida do player
                elif topic == "canonical.kyc_events":
                    await process_kyc_event(value, redis_client, ch_client, producer)
                elif topic == "canonical.responsible_gambling_events":
                    await process_responsible_gambling_event(value, redis_client, ch_client, producer)
                elif topic == "canonical.account_status_changes":
                    await process_account_status_change(value, redis_client, ch_client, producer)
                elif topic == "ingest.jobs":
                    await process_ingest_job(value, redis_client, ch_client, producer)
                elif topic == "ingest.jobs.reprocess":
                    await process_ingest_job(value, redis_client, ch_client, producer)
                EVENTS_PROCESSED.labels(topic=topic, status="processed").inc()

            except Exception as e:
                EVENTS_PROCESSED.labels(topic=topic, status="failed").inc()
                logger.error("message_processing_error", topic=topic, error=str(e))
            finally:
                PROCESSING_LATENCY.labels(topic=topic).observe(time.monotonic() - started)

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
    now = _dt.now(timezone.utc).replace(tzinfo=None)
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
    deposits_7d = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_7d)]
    deposits_30d = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_30d)]
    deposits_90d = [t for t in txns if t.get("txn_type") == "DEPOSIT" and in_win(t, cutoff_90d)]
    withdrawals_24h = [t for t in txns if t.get("txn_type") == "WITHDRAWAL" and in_win(t, cutoff_24h)]
    withdrawals_7d = [t for t in txns if t.get("txn_type") == "WITHDRAWAL" and in_win(t, cutoff_7d)]
    txns_24h     = [t for t in txns if in_win(t, cutoff_24h)]
    txns_7d      = [t for t in txns if in_win(t, cutoff_7d)]
    txns_30d     = [t for t in txns if in_win(t, cutoff_30d)]
    txns_90d     = [t for t in txns if in_win(t, cutoff_90d)]
    bets_7d      = [t for t in txns_7d if t.get("txn_type") == "BET"]
    bets_30d     = [t for t in txns_30d if t.get("txn_type") == "BET"]

    # Deposit velocity = deposits per hour in 24h
    dep_velocity = len(deposits_24h) / 24.0

    # Unique payment instruments in 7d
    unique_instruments_7d = len({t.get("instrument_id") for t in txns_7d if t.get("instrument_id")})

    # Night activity ratio (22:00–06:00 in 7d)
    def _is_night(t: dict) -> bool:
        h = t["ts"].hour
        return h >= 22 or h < 6

    night_ratio = len([t for t in txns_7d if _is_night(t)]) / max(len(txns_7d), 1)

    # Weekend activity ratio (Sat/Sun in 7d)
    weekend_ratio = len([t for t in txns_7d if t["ts"].weekday() >= 5]) / max(len(txns_7d), 1)

    # Multi-currency flag
    currencies = {t.get("currency", "BRL") for t in txns_7d}
    multi_currency = len(currencies) > 1

    # Win/loss ratio 30d
    wins_30d   = [b for b in bets_30d if b.get("result") == "WIN"]
    losses_30d = [b for b in bets_30d if b.get("result") == "LOSS"]
    win_loss_30d = len(wins_30d) / max(len(losses_30d), 1)

    odds_vals_7d = [float(b.get("odds")) for b in bets_7d if b.get("odds") not in (None, "")]
    avg_odds_7d = (sum(odds_vals_7d) / len(odds_vals_7d)) if odds_vals_7d else None

    # Chargeback rate 30d
    chargebacks_30d  = [t for t in txns_30d if t.get("is_chargeback")]
    chargeback_rate  = len(chargebacks_30d) / max(len(deposits_30d), 1)

    dep_ts_7d = sorted(t["ts"] for t in deposits_7d)
    wdraw_ts_7d = sorted(t["ts"] for t in withdrawals_7d)
    dep_to_wdraw_hours = []
    for dep_ts in dep_ts_7d:
        later = [w for w in wdraw_ts_7d if w > dep_ts]
        if later:
            dep_to_wdraw_hours.append((later[0] - dep_ts).total_seconds() / 3600.0)
    avg_dep_to_wdraw = (
        sum(dep_to_wdraw_hours) / len(dep_to_wdraw_hours) if dep_to_wdraw_hours else None
    )

    bonus_30d = [t for t in txns_30d if t.get("txn_type") == "BONUS"]
    bonus_ratio_30d = len(bonus_30d) / max(len(bonus_30d) + len(deposits_30d), 1)

    cashout_bets_7d = [
        b
        for b in bets_7d
        if b.get("cashout_amount") not in (None, "", 0, 0.0)
        or str(b.get("result") or b.get("status") or "").upper() in {"CASHOUT", "CASHED_OUT", "EARLY_CASHOUT"}
    ]
    cashout_ratio_7d = len(cashout_bets_7d) / max(len(bets_7d), 1)

    devices = sorted({t.get("device_id") for t in txns_90d if t.get("device_id")})
    banks = sorted({t.get("bank_id") for t in txns_90d if t.get("bank_id")})
    shared_device_score = min(max(len(devices) - 1, 0) / 10.0, 1.0)
    shared_instrument_score = min((max(len(devices) - 1, 0) * 0.4 + max(len(banks) - 1, 0) * 0.6) / 10.0, 1.0)
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
        "deposit_count_7d":      len(deposits_7d),
        "deposit_sum_24h":       float(sum(t.get("amount", 0) for t in deposits_24h)),
        "deposit_sum_7d":        float(sum(t.get("amount", 0) for t in deposits_7d)),
        "deposit_sum_30d":       float(sum(t.get("amount", 0) for t in deposits_30d)),
        "deposit_sum_90d":       float(sum(t.get("amount", 0) for t in deposits_90d)),
        "withdrawal_count_24h":  len(withdrawals_24h),
        "withdrawal_sum_24h":    float(sum(t.get("amount", 0) for t in withdrawals_24h)),
        "withdrawal_sum_7d":     float(sum(t.get("amount", 0) for t in withdrawals_7d)),
        "unique_instruments_7d": unique_instruments_7d,
        "unique_instruments_used_7d": unique_instruments_7d,
        "night_activity_ratio":  float(night_ratio),
        "weekend_activity_ratio": float(weekend_ratio),
        "avg_odds_bet_7d":       float(avg_odds_7d) if avg_odds_7d is not None else None,
        "inconsistent_currency_flag":   multi_currency,
        "win_loss_ratio_30d":    float(win_loss_30d),
        "chargeback_rate_30d":   float(chargeback_rate),
        "avg_time_between_deposit_and_withdrawal_7d": float(avg_dep_to_wdraw) if avg_dep_to_wdraw is not None else None,
        "avg_deposit_to_withdrawal_hours": float(avg_dep_to_wdraw) if avg_dep_to_wdraw is not None else None,
        "bonus_to_real_money_ratio_30d": float(bonus_ratio_30d),
        "bonus_to_real_ratio_30d": float(bonus_ratio_30d),
        "cashout_ratio_7d": float(cashout_ratio_7d),
        "shared_device_score":   float(shared_device_score),
        "shared_instrument_score": float(shared_instrument_score),
        "cluster_id":            cluster_id,
        "cluster_size":          max(len(devices), len(banks), 1),
        "txn_count_24h":         len(txns_24h),
        "bet_count_7d":          len(bets_7d),
        "bet_count_30d":         len(bets_30d),
    }
