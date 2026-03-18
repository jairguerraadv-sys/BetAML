"""
Rules Engine — BetAML
Consome: canonical.transactions, canonical.bets, features.player_daily
Para cada evento:
  1. Carrega regras ACTIVE do tenant (cache Redis, recarrega a cada 5 min)
  2. Constrói contexto DSL (event payload + features + player info)
  3. Avalia cada regra; em caso de match → gera Alert + RuleExecutionLog
  4. Publica scoring.alerts
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

# Garante que 'from libs.xxx import' funcione tanto no Docker (/app/libs montado)
# quanto em desenvolvimento local (raiz do projeto no PYTHONPATH)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logger = structlog.get_logger()

KAFKA_SERVERS    = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL     = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
RULE_CACHE_TTL   = 300  # 5 minutos

TOPICS = [
    "canonical.transactions",
    "canonical.bets",
    "features.player_daily",
]

FEATURE_EVIDENCE_KEYS = [
    "deposit_sum_24h",
    "deposit_sum_7d",
    "deposit_sum_30d",
    "deposit_count_24h",
    "zscore_current_deposit_vs_baseline",
    "new_payment_instrument_flag",
    "shared_device_count",
    "shared_device_score",
    "shared_instrument_score",
    "deposit_velocity",
    "night_activity_ratio",
    "weekend_activity_ratio",
    "chargeback_rate_30d",
    "cashout_ratio_7d",
    "unique_instruments_7d",
    "unique_instruments_used_7d",
    "bonus_to_real_ratio_30d",
    "bonus_to_real_money_ratio_30d",
    "cluster_id",
    "cluster_size",
]


# ──────────────────────────────────────────────────
# Rule cache (tenant_id → [(rule_id, rule_name, dsl, params, severity, scope, version)])
# ──────────────────────────────────────────────────
_rule_cache: dict[str, Any] = {}
_rule_cache_ts: dict[str, float] = {}

_db_engine = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_sync_db():
    """Sync DB session para carregar regras (rules_engine não precisa de async DB)."""
    import sqlalchemy as sa
    global _db_engine
    if _db_engine is None:
        sync_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")
        try:
            _db_engine = sa.create_engine(sync_url, pool_pre_ping=True)
        except Exception:
            # fallback: asyncpg não disponível, usar psycopg2
            _db_engine = None
    return _db_engine


async def load_rules(tenant_id: str) -> list[dict]:
    """Carrega regras do banco ou retorna cache."""
    now = time.time()
    if tenant_id in _rule_cache and (now - _rule_cache_ts.get(tenant_id, 0)) < RULE_CACHE_TTL:
        return _rule_cache[tenant_id]

    try:
        engine = await asyncio.to_thread(_get_sync_db)
        if not engine:
            return []
        import sqlalchemy as sa
        with engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT id, name, condition_dsl, params, severity, scope, version, weight "
                    "FROM rule_definitions WHERE tenant_id = :tid AND status = 'ACTIVE'"
                ),
                {"tid": tenant_id},
            )
            rules = [dict(row._mapping) for row in result]
        _rule_cache[tenant_id] = rules
        _rule_cache_ts[tenant_id] = now
        logger.info("rules_loaded", tenant_id=tenant_id, count=len(rules))
        return rules
    except Exception as e:
        logger.error("rules_load_failed", tenant_id=tenant_id, error=str(e))
        return _rule_cache.get(tenant_id, [])


async def load_macros(tenant_id: str) -> dict[str, str]:
    """Load rule macros (name → DSL expression) from Postgres with cache."""
    cache_key = f"macros:{tenant_id}"
    now = time.time()
    if cache_key in _rule_cache and (now - _rule_cache_ts.get(cache_key, 0)) < RULE_CACHE_TTL:
        return _rule_cache[cache_key]

    try:
        engine = await asyncio.to_thread(_get_sync_db)
        if not engine:
            return {}
        import sqlalchemy as sa
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("SELECT name, expression FROM rule_macros WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            macros = {r.name: r.expression for r in rows}
        _rule_cache[cache_key] = macros
        _rule_cache_ts[cache_key] = now
        return macros
    except Exception as e:
        logger.warning("macros_load_failed", error=str(e))
        return {}


async def load_player_lists(tenant_id: str) -> dict[str, set]:
    """Load PlayerList entries keyed by list name → set of values."""
    cache_key = f"player_lists:{tenant_id}"
    now = time.time()
    if cache_key in _rule_cache and (now - _rule_cache_ts.get(cache_key, 0)) < RULE_CACHE_TTL:
        return _rule_cache[cache_key]

    try:
        engine = await asyncio.to_thread(_get_sync_db)
        if not engine:
            return {}
        import sqlalchemy as sa
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text("""
                    SELECT pl.name, ple.value
                    FROM player_lists pl
                    JOIN player_list_entries ple ON ple.player_list_id = pl.id
                    WHERE pl.tenant_id = :tid
                """),
                {"tid": tenant_id},
            )
            lists: dict[str, set] = {}
            for row in rows:
                lists.setdefault(row.name, set()).add(row.value)
        _rule_cache[cache_key] = lists
        _rule_cache_ts[cache_key] = now
        return lists
    except Exception as e:
        logger.warning("player_lists_load_failed", error=str(e))
        return {}


async def load_compound_rules(tenant_id: str) -> list[dict]:
    """Load compound rules for the tenant."""
    cache_key = f"compound:{tenant_id}"
    now = time.time()
    if cache_key in _rule_cache and (now - _rule_cache_ts.get(cache_key, 0)) < RULE_CACHE_TTL:
        return _rule_cache[cache_key]

    try:
        engine = await asyncio.to_thread(_get_sync_db)
        if not engine:
            return []
        import sqlalchemy as sa
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT id, name, logic, component_rule_ids, score_weights, min_score_threshold "
                    "FROM compound_rules WHERE tenant_id = :tid AND is_active = true"
                ),
                {"tid": tenant_id},
            )
            compound = [dict(r._mapping) for r in rows]
        _rule_cache[cache_key] = compound
        _rule_cache_ts[cache_key] = now
        return compound
    except Exception as e:
        logger.warning("compound_rules_load_failed", error=str(e))
        return []


async def load_features(tenant_id: str, player_id: str, redis_client) -> dict:
    """Carrega features do Redis (online store)."""
    key = f"betaml:{tenant_id}:features:{player_id}"
    try:
        raw = await redis_client.hgetall(key)
        if raw:
            return {k: _try_float(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning("features_load_failed", error=str(e))
    return {}


def _try_float(v: Any) -> Any:
    # Safe-guard: None and empty string → 0.0 so DSL comparisons never crash
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        lowered = v.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            if any(ch in v for ch in (".", "e", "E")):
                return float(v)
            return int(v)
        except ValueError:
            return v  # preserve string feature values (e.g. cluster_id = "cluster:abc")
    try:
        return float(v)
    except (ValueError, TypeError):
        return v


async def evaluate_rules(
    envelope: dict[str, Any],
    features: dict[str, Any],
    rules: list[dict],
    macros: dict[str, str] | None = None,
    player_lists: dict[str, set] | None = None,
    compound_rules: list[dict] | None = None,
) -> list[dict]:
    """Avalia todas as regras ativas e retorna lista de matches."""
    from libs.dsl_parser import eval_dsl, DSLSyntaxError, DSLEvaluationError, expand_macros

    payload = envelope.get("payload", {})
    entity_type = envelope.get("entity_type", "").upper()

    ctx_transaction: dict = {}
    ctx_bet: dict = {}
    ctx_player: dict = {}

    if entity_type == "TRANSACTION":
        ctx_transaction = {
            "amount":   float(payload.get("amount", 0)),
            "type":     payload.get("type", ""),
            "method":   payload.get("method", ""),
            "status":   payload.get("status", ""),
            "currency": payload.get("currency", "BRL"),
        }
        player_id = payload.get("player_id", "")
    elif entity_type == "BET":
        ctx_bet = {
            "stakeAmount": float(payload.get("stake_amount", 0)),
            "odds":        float(payload.get("odds") or 0),
            "channel":     payload.get("channel", ""),
        }
        player_id = payload.get("player_id", "")
    else:
        return []

    ctx_player = {
        "pepFlag":               features.get("pep_flag", False),
        "pep_flag":              features.get("pep_flag", False),
        "declaredIncomeMonthly": features.get("declared_income_monthly", 0),
        "declared_income_monthly": features.get("declared_income_monthly", 0),
    }

    scope_map = {"TRANSACTION": "TRANSACTION", "BET": "BET"}
    event_scope = scope_map.get(entity_type, entity_type)

    matches: list[dict] = []
    rule_scores: dict[int, float] = {}  # rule_id → 0.0 or 1.0 (matched)

    for rule in rules:
        if rule["scope"] not in (event_scope, "PLAYER"):
            continue

        ctx = {
            "transaction":  ctx_transaction,
            "bet":          ctx_bet,
            "player":       ctx_player,
            "features":     features,
            "params":       rule.get("params") or {},
            "player_lists": player_lists or {},
        }

        # Expand macros in DSL expression
        dsl_expr = rule["condition_dsl"]
        if macros:
            try:
                dsl_expr = expand_macros(dsl_expr, macros)
            except DSLSyntaxError as e:
                logger.warning("macro_expansion_failed", rule_id=str(rule["id"]), error=str(e))

        start = time.monotonic()
        try:
            matched = eval_dsl(dsl_expr, ctx)
            eval_ms = int((time.monotonic() - start) * 1000)
        except (DSLSyntaxError, DSLEvaluationError) as e:
            logger.warning("dsl_eval_error", rule_id=str(rule["id"]), error=str(e))
            matched = False
            eval_ms = 0

        rule_weight = float(rule.get("weight") or 1.0)
        rule_scores[rule["id"]] = rule_weight if matched else 0.0

        if matched:
            matches.append({
                "rule":             rule,
                "eval_ms":          eval_ms,
                "rule_weight":      rule_weight,
                "context_snapshot": {k: v for k, v in ctx.items()
                                     if k not in ("features", "player_lists")},
                "features_snapshot": {k: features.get(k) for k in FEATURE_EVIDENCE_KEYS if k in features},
            })

    # ── Compound rule evaluation ─────────────────────────────────────────────
    for crule in (compound_rules or []):
        component_ids = crule.get("component_rule_ids") or []
        score_weights = crule.get("score_weights") or {}
        min_threshold = crule.get("min_score_threshold") or 0.5

        # Compute weighted composite score from component rules
        total_weight = 0.0
        weighted_score = 0.0
        for rid in component_ids:
            w = float(score_weights.get(str(rid), 1.0))
            total_weight += w
            weighted_score += rule_scores.get(rid, 0.0) * w

        composite = weighted_score / max(total_weight, 1e-9)

        if composite >= min_threshold:
            matches.append({
                "rule": {
                    "id":            crule["id"],
                    "name":          crule["name"],
                    "condition_dsl": crule["logic"],
                    "severity":      "HIGH",
                    "scope":         event_scope,
                    "version":       1,
                    "weight":        1.0,
                    "is_compound":   True,
                },
                "eval_ms":           0,
                "rule_weight":       1.0,
                "composite_score":   composite,
                "context_snapshot":  {},
                "features_snapshot": {},
            })

    return matches


async def publish_alert(
    envelope: dict[str, Any],
    match: dict[str, Any],
    producer,
    db_write_queue: asyncio.Queue,
):
    rule = match["rule"]
    payload = envelope.get("payload", {})
    player_id = payload.get("player_id", "")
    tenant_id = envelope.get("tenant_id", "")

    alert_id = str(uuid.uuid4())
    alert_msg = {
        "alert_id":       alert_id,
        "tenant_id":      tenant_id,
        "player_id":      player_id,
        "alert_type":     "RULE",
        "severity":       rule["severity"],
        "title":          f"{rule['name']} — {player_id[:8]}",
        "description":    f"Regra '{rule['name']}' disparada para player {player_id}",
        "rule_id":        str(rule["id"]),
        "rule_version":   rule.get("version", 1),
        "source_event_id": envelope.get("event_id", ""),
        "evidence": {
            "rule_id":             str(rule["id"]),
            "rule_version":        rule.get("version", 1),
            "triggered_condition": rule["condition_dsl"],
            "feature_snapshot":    match.get("features_snapshot", {}),
            "threshold_values":    rule.get("params", {}),
        },
        "created_at": _utcnow().isoformat(),
        "schema_version": 1,
    }

    # Publicar no Kafka
    await producer.send("scoring.alerts", alert_msg, key=alert_id)

    # Enfileirar para escrita async no Postgres
    await db_write_queue.put({
        "type": "alert",
        "alert": alert_msg,
        "eval_ms": match["eval_ms"],
        "context_snapshot": match.get("context_snapshot"),
        "matched": True,
    })

    logger.info(
        "alert_published",
        alert_id=alert_id, rule=rule["name"], player_id=player_id,
        severity=rule["severity"],
    )


async def db_writer(queue: asyncio.Queue, db_url: str):
    """Task separada que escreve alerts + RuleExecutionLogs no Postgres."""
    import sqlalchemy as sa
    sync_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
    try:
        engine = sa.create_engine(sync_url, pool_pre_ping=True)
    except Exception as e:
        logger.error("db_writer_init_failed", error=str(e))
        while True:
            item = await queue.get()
            queue.task_done()
        return

    # Mapeamento severity → risk_score mínimo para o player
    _SEVERITY_SCORE = {"CRITICAL": 0.95, "HIGH": 0.80, "MEDIUM": 0.55, "LOW": 0.30}

    def _write(item: dict):
        alert = item["alert"]
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with engine.begin() as conn:
            # Define contexto do tenant para respeitar FORCE ROW LEVEL SECURITY
            conn.execute(
                sa.text("SET LOCAL app.current_tenant = :tid"),
                {"tid": alert["tenant_id"]},
            )
            # Upsert alert
            conn.execute(sa.text("""
                INSERT INTO alerts
                    (id, tenant_id, player_id, rule_id, alert_type, severity, status,
                     title, description, evidence, source_event_id, created_at)
                VALUES
                    (:id, :tenant_id, :player_id, :rule_id, :alert_type, :severity, 'OPEN',
                     :title, :description, :evidence, :source_event_id, :created_at)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id":             alert["alert_id"],
                "tenant_id":      alert["tenant_id"],
                "player_id":      alert.get("player_id") or None,
                "rule_id":        alert.get("rule_id") or None,
                "alert_type":     alert["alert_type"],
                "severity":       alert["severity"],
                "title":          alert["title"],
                "description":    alert.get("description", ""),
                "evidence":       json.dumps(alert.get("evidence", {})),
                "source_event_id": alert.get("source_event_id"),
                "created_at":     created_at,
            })

            # Auto-case: alertas CRITICAL geram (ou reaproveitam) caso OPEN/IN_REVIEW do player.
            if alert.get("severity") == "CRITICAL" and alert.get("player_id"):
                existing_case = conn.execute(sa.text("""
                    SELECT id
                    FROM cases
                    WHERE tenant_id = :tenant_id
                      AND player_id = :player_id
                      AND status IN ('OPEN', 'IN_REVIEW')
                    ORDER BY created_at DESC
                    LIMIT 1
                """), {
                    "tenant_id": alert["tenant_id"],
                    "player_id": alert["player_id"],
                }).fetchone()

                case_id = str(existing_case.id) if existing_case else str(uuid.uuid4())
                if not existing_case:
                    conn.execute(sa.text("""
                        INSERT INTO cases (
                            id, tenant_id, player_id, title, description, status,
                            severity, priority, auto_created, auto_created_reason,
                            source_alert_id, created_at, updated_at
                        ) VALUES (
                            :id, :tenant_id, :player_id, :title, :description, 'OPEN',
                            :severity, 'HIGH', true, :reason,
                            :source_alert_id, :created_at, :created_at
                        )
                    """), {
                        "id": case_id,
                        "tenant_id": alert["tenant_id"],
                        "player_id": alert["player_id"],
                        "title": f"Auto-case: {alert.get('title', 'Alerta crítico')}",
                        "description": "Caso criado automaticamente a partir de alerta CRITICAL.",
                        "severity": alert["severity"],
                        "reason": f"rules_engine:auto_case alert_id={alert['alert_id']} severity={alert['severity']}",
                        "source_alert_id": alert["alert_id"],
                        "created_at": created_at,
                    })
                    conn.execute(sa.text("""
                        INSERT INTO case_events (
                            id, case_id, tenant_id, event_type, content, created_at
                        ) VALUES (
                            :id, :case_id, :tenant_id, :event_type, CAST(:content AS jsonb), :created_at
                        )
                    """), {
                        "id": str(uuid.uuid4()),
                        "case_id": case_id,
                        "tenant_id": alert["tenant_id"],
                        "event_type": "AUTO_CREATED_FROM_ALERT",
                        "content": json.dumps({
                            "alert_id": alert["alert_id"],
                            "severity": alert["severity"],
                            "rule_id": alert.get("rule_id"),
                        }),
                        "created_at": created_at,
                    })

                conn.execute(sa.text("""
                    UPDATE alerts
                    SET case_id = :case_id,
                        updated_at = NOW()
                    WHERE id = :alert_id
                      AND tenant_id = :tenant_id
                      AND (case_id IS NULL OR case_id <> :case_id)
                """), {
                    "case_id": case_id,
                    "alert_id": alert["alert_id"],
                    "tenant_id": alert["tenant_id"],
                })

            # RuleExecutionLog
            conn.execute(sa.text("""
                INSERT INTO rule_execution_logs
                    (id, tenant_id, rule_id, rule_version, source_event_id, player_id,
                     matched, evaluation_ms, context_snapshot)
                VALUES
                    (:id, :tenant_id, :rule_id, :rule_version, :source_event_id, :player_id,
                     :matched, :eval_ms, :ctx)
            """), {
                "id":             str(uuid.uuid4()),
                "tenant_id":      alert["tenant_id"],
                "rule_id":        alert.get("rule_id") or None,
                "rule_version":   alert.get("rule_version", 1),
                "source_event_id": alert.get("source_event_id", ""),
                "player_id":      alert.get("player_id") or None,
                "matched":        item.get("matched", True),
                "eval_ms":        item.get("eval_ms", 0),
                "ctx":            json.dumps(item.get("context_snapshot") or {}),
            })
            # Atualizar risk_score e risk_band do player com base na severidade do alerta
            # (apenas sobe o score, nunca diminui — persistência de risco)
            if alert.get("player_id"):
                new_score = _SEVERITY_SCORE.get(alert.get("severity", "LOW"), 0.30)
                conn.execute(sa.text("""
                    UPDATE players
                    SET risk_score = GREATEST(risk_score, :score),
                        risk_band  = CASE
                            WHEN GREATEST(risk_score, :score) >= 0.70 THEN 'HIGH'
                            WHEN GREATEST(risk_score, :score) >= 0.35 THEN 'MEDIUM'
                            ELSE 'LOW'
                        END,
                        last_scored_at = NOW()
                    WHERE id = :player_id AND tenant_id = :tenant_id
                """), {
                    "score":     new_score,
                    "player_id": alert["player_id"],
                    "tenant_id": alert["tenant_id"],
                })

    while True:
        item = await queue.get()
        try:
            await asyncio.to_thread(_write, item)
        except Exception as e:
            logger.error("db_write_error", error=str(e))
        finally:
            queue.task_done()


async def main():
    from libs.clients import KafkaConsumerClient, KafkaProducerClient, RedisClient

    redis_client = RedisClient(REDIS_URL)
    await redis_client.connect()

    producer = KafkaProducerClient(KAFKA_SERVERS)
    await producer.start()

    consumer = KafkaConsumerClient(
        topics=TOPICS,
        group_id="rules-engine",
        bootstrap_servers=KAFKA_SERVERS,
    )
    await consumer.start()

    db_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    db_task = asyncio.create_task(db_writer(db_queue, DATABASE_URL))

    logger.info("rules_engine_started", topics=TOPICS)

    try:
        async for msg in consumer:
            try:
                value = msg.value if isinstance(msg.value, dict) else json.loads(msg.value)
                topic = msg.topic

                # features.player_daily: apenas atualiza cache/estado, sem regras
                if topic == "features.player_daily":
                    continue

                tenant_id = value.get("tenant_id")
                payload   = value.get("payload", {})
                player_id = payload.get("player_id") or payload.get("playerId", "")

                if not tenant_id:
                    continue

                rules          = await load_rules(tenant_id)
                features       = await load_features(tenant_id, player_id, redis_client)
                macros         = await load_macros(tenant_id)
                player_lists   = await load_player_lists(tenant_id)
                compound_rules = await load_compound_rules(tenant_id)
                matches        = await evaluate_rules(
                    value, features, rules,
                    macros=macros,
                    player_lists=player_lists,
                    compound_rules=compound_rules,
                )

                for match in matches:
                    await publish_alert(value, match, producer, db_queue)

            except Exception as e:
                logger.error("message_processing_error", topic=msg.topic, error=str(e))

    finally:
        await consumer.stop()
        await producer.stop()
        await redis_client.disconnect()
        db_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
