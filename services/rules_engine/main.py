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
import logging
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Any

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

KAFKA_SERVERS    = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL     = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
ML_SERVICE_URL   = os.getenv("ML_SERVICE_URL", "http://ml-service:8001")
RULE_CACHE_TTL   = 300  # 5 minutos
METRICS_PORT     = int(os.getenv("METRICS_PORT", "8002"))
HEALTH_PORT      = int(os.getenv("HEALTH_PORT", "8012"))

# ── Health HTTP server (liveness/readiness probes para K8s) ──────────────────
_healthy = False


class _HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler para /health/live e /health/ready."""

    def do_GET(self):  # noqa: N802
        if self.path == "/health/live":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"live"}')
        elif self.path == "/health/ready":
            if _healthy:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ready"}')
            else:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"status":"starting"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass


def _start_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("health_server_started", port=HEALTH_PORT)

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

DEFAULT_SCORING_CONFIG = {
    "rule_weight": 0.4,
    "ml_weight": 0.4,
    "network_weight": 0.2,
    "low_threshold": 30.0,
    "medium_threshold": 60.0,
    "high_threshold": 80.0,
    "critical_threshold": 95.0,
}

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
    "betaml_rules_events_processed_total",
    "Total de eventos processados pelo rules engine",
    ["topic", "status"],
)

RULES_PROCESSING_LATENCY = _get_or_create_metric(
    Histogram,
    "betaml_rules_processing_seconds",
    "Latência de processamento do rules engine por tópico",
    ["topic"],
)

ALERTS_GENERATED = _get_or_create_metric(
    Counter,
    "betaml_rules_alerts_generated_total",
    "Alertas gerados pelo rules engine por severidade e tenant",
    ["severity", "tenant_id"],
)

ML_SCORING_FAILURES = _get_or_create_metric(
    Counter,
    "betaml_ml_scoring_failures_total",
    "Falhas de scoring ML observadas pelo rules engine",
    ["tenant_id", "reason"],
)

CONSUMER_LAG = _get_or_create_metric(
    Gauge,
    "betaml_rules_consumer_lag_messages",
    "Lag estimado do consumer do rules engine por tópico",
    ["group_id", "topic"],
)


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
                    "SELECT id, name, logic, n_threshold, component_rule_ids, "
                    "severity_mode, fixed_severity, score_weights, min_score_threshold "
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


async def load_scoring_config(tenant_id: str) -> dict[str, Any]:
    cache_key = f"scoring_config:{tenant_id}"
    now = time.time()
    if cache_key in _rule_cache and (now - _rule_cache_ts.get(cache_key, 0)) < RULE_CACHE_TTL:
        return _rule_cache[cache_key]

    cfg = dict(DEFAULT_SCORING_CONFIG)
    try:
        engine = await asyncio.to_thread(_get_sync_db)
        if not engine:
            return cfg
        import sqlalchemy as sa
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT rule_weight, ml_weight, network_weight, low_threshold, medium_threshold, "
                    "high_threshold, critical_threshold "
                    "FROM scoring_configs WHERE tenant_id = :tid LIMIT 1"
                ),
                {"tid": tenant_id},
            ).fetchone()
            if row:
                cfg.update({k: float(v) if v is not None else cfg[k] for k, v in dict(row._mapping).items()})
        _rule_cache[cache_key] = cfg
        _rule_cache_ts[cache_key] = now
        return cfg
    except Exception as e:
        logger.warning("scoring_config_load_failed", tenant_id=tenant_id, error=str(e))
        return cfg


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


def _severity_rank(severity: str) -> int:
    order = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return order.get(str(severity or "LOW").upper(), 1)


def _severity_from_composite(score: float) -> str:
    if score >= 0.95:
        return "CRITICAL"
    if score >= 0.80:
        return "HIGH"
    if score >= 0.60:
        return "MEDIUM"
    return "LOW"


def _severity_from_scoring_config(score_100: float, cfg: dict[str, Any]) -> str:
    if score_100 >= float(cfg.get("critical_threshold", 95.0)):
        return "CRITICAL"
    if score_100 >= float(cfg.get("high_threshold", 80.0)):
        return "HIGH"
    if score_100 >= float(cfg.get("medium_threshold", 60.0)):
        return "MEDIUM"
    return "LOW"


def _normalize_ml_features(features: dict[str, Any]) -> dict[str, float | bool | str]:
    normalized: dict[str, float | bool | str] = {}
    for k, v in (features or {}).items():
        if isinstance(v, bool):
            normalized[k] = v
            continue
        if isinstance(v, (int, float)):
            normalized[k] = float(v)
            continue
        if isinstance(v, str):
            low = v.lower()
            if low in {"true", "false"}:
                normalized[k] = low == "true"
                continue
            try:
                normalized[k] = float(v)
            except ValueError:
                normalized[k] = v
    return normalized


def _score_ml_sync(tenant_id: str, player_id: str, features: dict[str, Any]) -> dict[str, Any]:
    ctx = structlog.contextvars.get_contextvars()
    headers = {"Content-Type": "application/json"}
    request_id = ctx.get("request_id")
    event_id = ctx.get("event_id")
    if isinstance(request_id, str) and request_id:
        headers["X-Request-ID"] = request_id
    if isinstance(event_id, str) and event_id:
        headers["X-Event-ID"] = event_id
    body = {
        "tenant_id": tenant_id,
        "player_id": player_id,
        "features": _normalize_ml_features(features),
    }
    req = urllib.request.Request(
        url=f"{ML_SERVICE_URL.rstrip('/')}/score",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            return {
                "anomaly_score": float(parsed.get("anomaly_score") or 0.0),
                "is_anomaly": bool(parsed.get("is_anomaly") or False),
                "model_id": parsed.get("model_id"),
                "top_drivers": parsed.get("top_drivers") or [],
            }
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        ML_SCORING_FAILURES.labels(tenant_id=tenant_id, reason=exc.__class__.__name__).inc()
        logger.warning("ml_score_unavailable", tenant_id=tenant_id, player_id=player_id, error=str(exc))
        return {"anomaly_score": 0.0, "is_anomaly": False, "model_id": None, "top_drivers": []}


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
            "productType": payload.get("product_type", "SPORTSBOOK"),
            "gameCategory": payload.get("game_category", ""),
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
        operator = str(crule.get("operator") or crule.get("logic") or "AND").upper()
        n_threshold = int(crule.get("n_threshold") or 1)
        severity_mode = str(crule.get("severity_mode") or "MAX").upper()
        fixed_severity = crule.get("fixed_severity")

        # Compute weighted composite score from component rules
        total_weight = 0.0
        weighted_score = 0.0
        matched_components = 0
        max_component_severity = "LOW"
        for rid in component_ids:
            w = float(score_weights.get(str(rid), 1.0))
            total_weight += w
            score = rule_scores.get(rid, 0.0)
            weighted_score += score * w
            if score > 0:
                matched_components += 1
                component_rule = next((r for r in rules if str(r.get("id")) == str(rid)), None)
                if component_rule and _severity_rank(component_rule.get("severity", "LOW")) > _severity_rank(max_component_severity):
                    max_component_severity = component_rule.get("severity", "LOW")

        composite = weighted_score / max(total_weight, 1e-9)
        should_match = False
        if operator == "AND":
            should_match = matched_components == len(component_ids) and len(component_ids) > 0
        elif operator == "OR":
            should_match = matched_components >= 1
        elif operator == "N_OF_M":
            should_match = matched_components >= max(n_threshold, 1)
        else:
            should_match = composite >= min_threshold

        if should_match and composite >= min_threshold:
            matches.append({
                "rule": {
                    "id":            crule["id"],
                    "name":          crule["name"],
                    "condition_dsl": f"{operator}({matched_components}/{len(component_ids)})",
                    "severity":      fixed_severity if severity_mode == "FIXED" and fixed_severity else max_component_severity,
                    "scope":         event_scope,
                    "version":       1,
                    "weight":        1.0,
                    "is_compound":   True,
                    "compound_rule_id": str(crule["id"]),
                    "severity_mode": severity_mode,
                },
                "eval_ms":           0,
                "rule_weight":       1.0,
                "composite_score":   composite,
                "context_snapshot":  {"matched_components": matched_components, "operator": operator, "n_threshold": n_threshold},
                "features_snapshot": {},
            })

    return matches


async def publish_alert(
    envelope: dict[str, Any],
    match: dict[str, Any],
    producer,
    db_write_queue: asyncio.Queue,
    ml_signal: dict[str, Any] | None = None,
    scoring_cfg: dict[str, Any] | None = None,
):
    rule = match["rule"]
    payload = envelope.get("payload", {})
    player_id = payload.get("player_id", "")
    tenant_id = envelope.get("tenant_id", "")

    ml_signal = ml_signal or {}
    scoring_cfg = scoring_cfg or dict(DEFAULT_SCORING_CONFIG)
    rule_weight = float(match.get("rule_weight") or 1.0)
    rule_score = min(max(rule_weight, 0.0), 1.0)
    rule_component_weight = float(scoring_cfg.get("rule_weight", 0.4))
    ml_weight = float(scoring_cfg.get("ml_weight", 0.4))
    network_component_weight = float(scoring_cfg.get("network_weight", 0.2))
    anomaly_score = float(ml_signal.get("anomaly_score") or 0.0)
    network_score = float(
        match.get("features_snapshot", {}).get("shared_instrument_score")
        or match.get("features_snapshot", {}).get("shared_device_score")
        or 0.0
    )
    composite_score = max(
        0.0,
        min(
            1.0,
            (rule_score * rule_component_weight)
            + (anomaly_score * ml_weight)
            + (network_score * network_component_weight),
        ),
    )
    risk_score_100 = composite_score * 100.0
    computed_severity = _severity_from_scoring_config(risk_score_100, scoring_cfg)
    base_severity = str(rule.get("severity") or "LOW").upper()
    final_severity = computed_severity if _severity_rank(computed_severity) > _severity_rank(base_severity) else base_severity

    alert_id = str(uuid.uuid4())
    # GAP-R2/R3: propagar ingest_mode e backfill_job_id do envelope para o alerta
    _ingest_meta = envelope.get("ingest_metadata") or {}
    _ingest_mode = str(_ingest_meta.get("ingest_mode") or envelope.get("ingest_mode") or "incremental")
    _backfill_job_id = _ingest_meta.get("backfill_job_id") or envelope.get("backfill_job_id")
    alert_msg = {
        "alert_id":       alert_id,
        "tenant_id":      tenant_id,
        "player_id":      player_id,
        "alert_type":     "COMPOSITE" if (anomaly_score > 0 or rule.get("is_compound")) else "RULE",
        "severity":       final_severity,
        "title":          f"{rule['name']} — {player_id[:8]}",
        "ingest_mode":    _ingest_mode,
        "backfill_job_id": _backfill_job_id,
        "description":    f"Regra '{rule['name']}' disparada para player {player_id}",
        "rule_id":        None if rule.get("is_compound") else str(rule["id"]),
        "compound_rule_id": str(rule.get("compound_rule_id") or rule["id"]) if rule.get("is_compound") else None,
        "rule_version":   rule.get("version", 1),
        "source_event_id": envelope.get("event_id", ""),
        "anomaly_score":  round(anomaly_score, 4),
        "composite_score": round(composite_score, 4),
        "risk_score": round(risk_score_100, 2),
        "score_breakdown": {
            "rule_score": round(rule_score, 4),
            "ml_anomaly_score": round(anomaly_score, 4),
            "network_score": round(network_score, 4),
            "rule_weight": rule_component_weight,
            "ml_weight": ml_weight,
            "network_weight": network_component_weight,
            "rule_contribution": round(rule_score * rule_component_weight * 100.0, 2),
            "ml_contribution": round(anomaly_score * ml_weight * 100.0, 2),
            "network_contribution": round(network_score * network_component_weight * 100.0, 2),
            "risk_score": round(risk_score_100, 2),
        },
        "rule_weight":    rule_component_weight,
        "ml_weight":      ml_weight,
        "network_weight": network_component_weight,
        "evidence": {
            "rule_id":             str(rule["id"]),
            "rule_version":        rule.get("version", 1),
            "triggered_condition": rule["condition_dsl"],
            "feature_snapshot":    match.get("features_snapshot", {}),
            "threshold_values":    rule.get("params", {}),
            "model_id":            ml_signal.get("model_id"),
            "top_drivers":         ml_signal.get("top_drivers") or [],
        },
        "created_at": _utcnow().isoformat(),
        "schema_version": 1,
    }

    # Publicar no Kafka
    await producer.send("scoring.alerts", alert_msg, key=alert_id)
    ALERTS_GENERATED.labels(severity=final_severity, tenant_id=str(tenant_id or "unknown")).inc()

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
        severity=final_severity,
        anomaly_score=round(anomaly_score, 4),
        composite_score=round(composite_score, 4),
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
        ingest_mode    = alert.get("ingest_mode", "incremental")
        backfill_job_id = alert.get("backfill_job_id")
        created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with engine.begin() as conn:
            # Define contexto do tenant para respeitar FORCE ROW LEVEL SECURITY
            conn.execute(
                sa.text("SET LOCAL app.current_tenant = :tid"),
                {"tid": alert["tenant_id"]},
            )
            # GAP-R2: em modo backfill, registrar alerta mas NÃO gerar auto-case
            # (evita poluição do dashboard com casos de importação histórica em massa).
            # Em modo 'reprocess', criar caso normalmente — pode ter havido supressão anterior.
            skip_auto_case = (ingest_mode == "backfill")

            # Upsert alert — agora com ingest_mode + backfill_job_id (GAP-R3)
            conn.execute(sa.text("""
                INSERT INTO alerts
                    (id, tenant_id, player_id, rule_id, compound_rule_id, alert_type, severity, status,
                     title, description, evidence, source_event_id, anomaly_score,
                     composite_score, score_breakdown, rule_weight, ml_weight,
                     network_weight, ingest_mode, backfill_job_id, created_at)
                VALUES
                    (:id, :tenant_id, :player_id, :rule_id, :compound_rule_id, :alert_type, :severity, 'OPEN',
                     :title, :description, :evidence, :source_event_id, :anomaly_score,
                     :composite_score, CAST(:score_breakdown AS jsonb), :rule_weight,
                     :ml_weight, :network_weight, :ingest_mode, :backfill_job_id, :created_at)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id":             alert["alert_id"],
                "tenant_id":      alert["tenant_id"],
                "player_id":      alert.get("player_id") or None,
                "rule_id":        alert.get("rule_id") or None,
                "compound_rule_id": alert.get("compound_rule_id") or None,
                "alert_type":     alert["alert_type"],
                "severity":       alert["severity"],
                "title":          alert["title"],
                "description":    alert.get("description", ""),
                "evidence":       json.dumps(alert.get("evidence", {})),
                "source_event_id": alert.get("source_event_id"),
                "anomaly_score":  alert.get("anomaly_score"),
                "composite_score": alert.get("composite_score"),
                "score_breakdown": json.dumps(alert.get("score_breakdown") or {}),
                "rule_weight":    alert.get("rule_weight"),
                "ml_weight":      alert.get("ml_weight"),
                "network_weight": alert.get("network_weight"),
                "ingest_mode":    ingest_mode,
                "backfill_job_id": backfill_job_id or None,
                "created_at":     created_at,
            })

            # Auto-case: alertas CRITICAL geram (ou reaproveitam) caso OPEN/IN_REVIEW do player.
            # GAP-C2: skip em backfill + enriquecer caso existente em vez de criar duplicata
            if alert.get("severity") == "CRITICAL" and alert.get("player_id") and not skip_auto_case:
                existing_case = conn.execute(sa.text("""
                    SELECT id
                    FROM cases
                    WHERE tenant_id = :tenant_id
                      AND player_id = :player_id
                      AND status IN ('OPEN', 'IN_REVIEW', 'INVESTIGATING', 'PENDING_REVIEW')
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
                            source_alert_id, ingest_mode, backfill_job_id, created_at, updated_at
                        ) VALUES (
                            :id, :tenant_id, :player_id, :title, :description, 'OPEN',
                            :severity, 'HIGH', true, :reason,
                            :source_alert_id, :ingest_mode, :backfill_job_id, :created_at, :created_at
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
                        "ingest_mode": ingest_mode,
                        "backfill_job_id": backfill_job_id or None,
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
                else:
                    # GAP-C2: caso existente → enriquecer com CaseEvent em vez de criar duplicata
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
                        "event_type": "ALERT_LINKED_TO_EXISTING_CASE",
                        "content": json.dumps({
                            "alert_id": alert["alert_id"],
                            "severity": alert["severity"],
                            "rule_id": alert.get("rule_id"),
                            "composite_score": alert.get("composite_score"),
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
                new_score = float(alert.get("composite_score") or _SEVERITY_SCORE.get(alert.get("severity", "LOW"), 0.30))
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
    global _healthy
    from libs.clients import KafkaConsumerClient, KafkaProducerClient, RedisClient

    start_http_server(METRICS_PORT)
    _start_health_server()
    init_opentelemetry_stub("rules-engine")

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
    _healthy = True

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
                    CONSUMER_LAG.labels(group_id="rules-engine", topic=topic).set(max(highwater - offset - 1, 0))

                # features.player_daily: apenas atualiza cache/estado, sem regras
                if topic == "features.player_daily":
                    EVENTS_PROCESSED.labels(topic=topic, status="skipped").inc()
                    continue

                tenant_id = value.get("tenant_id")
                payload   = value.get("payload", {})
                player_id = payload.get("player_id") or payload.get("playerId", "")

                if not tenant_id:
                    continue

                rules          = await load_rules(tenant_id)
                features       = await load_features(tenant_id, player_id, redis_client)
                ml_signal      = await asyncio.to_thread(_score_ml_sync, tenant_id, player_id, features)
                macros         = await load_macros(tenant_id)
                player_lists   = await load_player_lists(tenant_id)
                compound_rules = await load_compound_rules(tenant_id)
                scoring_cfg    = await load_scoring_config(tenant_id)
                matches        = await evaluate_rules(
                    value, features, rules,
                    macros=macros,
                    player_lists=player_lists,
                    compound_rules=compound_rules,
                )

                for match in matches:
                    await publish_alert(value, match, producer, db_queue, ml_signal=ml_signal, scoring_cfg=scoring_cfg)
                EVENTS_PROCESSED.labels(topic=topic, status="processed").inc()

            except Exception as e:
                EVENTS_PROCESSED.labels(topic=topic, status="failed").inc()
                logger.error("message_processing_error", topic=topic, error=str(e))
            finally:
                RULES_PROCESSING_LATENCY.labels(topic=topic).observe(time.monotonic() - started)

    finally:
        await consumer.stop()
        await producer.stop()
        await redis_client.disconnect()
        db_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
