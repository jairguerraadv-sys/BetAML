"""
ML Service — BetAML
REST API: POST /score      (online scoring + A/B champion/challenger)
         POST /score/shap  (SHAP explainability for a score)
         POST /train       (trigger training job — IsolationForest OR structuring OR graph)
         GET  /models      (listar model registry)
         POST /models/reload
         POST /models/{model_id}/ab-compare   (A/B metrics)
Modelos:
  - IsolationForest          : unsupervised anomaly (original)
  - StructuringDetector      : supervised binary (uses labeled True Positives)
  - GraphClustering (DBSCAN) : shared-device / shared-instrument clusters
  - RecurrenceEstimator      : device+IP+temporal pattern matching
Artefatos: MinIO   (betaml-models/{tenant_id}/{model_id}.pkl)
Registro:  Postgres (model_registry)
"""
from __future__ import annotations

import io
import hashlib
import json
import os
import pickle
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import joblib
import numpy as np
import structlog
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import Response
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from libs.telemetry import init_opentelemetry_stub

logger = structlog.get_logger()

ML_SCORING_FAILURES = Counter(
    "betaml_ml_service_scoring_failures_total",
    "Falhas de scoring no ml_service por tenant e motivo",
    ["tenant_id", "reason"],
)

FEATURE_ALIASES = {
    "unique_instruments_used_7d": "unique_instruments_7d",
    "bonus_to_real_money_ratio_30d": "bonus_to_real_ratio_30d",
}

DATABASE_URL   = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minio123")
BUCKET_MODELS  = os.getenv("ML_MODEL_BUCKET", "betaml-models")
ENVIRONMENT    = os.getenv("ENVIRONMENT", "development").strip().lower()
ML_ALLOW_SYNTHETIC_TRAINING = os.getenv("ML_ALLOW_SYNTHETIC_TRAINING", "").strip().lower() in {
    "1", "true", "yes", "on",
}
NON_PROD_ENVIRONMENTS = {"development", "test"}

# ──────────────────────────────────────────────────────────────────────────────
# Feature columns usados no treinamento (mesma ordem no score)
# ──────────────────────────────────────────────────────────────────────────────
FEATURE_COLS = [
    "deposit_sum_24h",
    "deposit_sum_7d",
    "deposit_sum_30d",
    "deposit_count_24h",
    "deposit_count_7d",
    "withdrawal_sum_24h",
    "withdrawal_sum_7d",
    "avg_bet_stake_7d",
    "bet_count_7d",
    "bet_count_24h",
    "win_loss_ratio_7d",
    "zscore_current_deposit_vs_baseline",
    "new_payment_instrument_flag",
    "shared_device_count",
    "unique_ips_24h",
    "baseline_deposit_avg_30d",
    "baseline_deposit_std_30d",
    # M2 new features
    "deposit_velocity",
    "unique_instruments_7d",
    "night_activity_ratio",
    "weekend_activity_ratio",
    "chargeback_rate_30d",
    "bonus_to_real_ratio_30d",
    "cashout_ratio_7d",
    "shared_instrument_score",
]

# Structuring detector uses a supervised feature set
STRUCTURING_COLS = [
    "deposit_sum_24h", "deposit_count_24h",
    "deposit_sum_7d",  "deposit_count_7d",
    "withdrawal_sum_7d", "cashout_ratio_7d",
    "unique_instruments_7d", "deposit_velocity",
    "night_activity_ratio", "chargeback_rate_30d",
]

# Graph / DBSCAN uses only network features
GRAPH_COLS = [
    "shared_device_count", "shared_instrument_score",
    "unique_instruments_7d", "inconsistent_currency_flag",
]

# Cache de modelos carregados por tenant
_model_cache: dict[str, dict[str, Any]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_non_prod_environment() -> bool:
    return ENVIRONMENT in NON_PROD_ENVIRONMENTS


def _allow_synthetic_training() -> bool:
    return _is_non_prod_environment() or ML_ALLOW_SYNTHETIC_TRAINING


def _clear_tenant_model_cache(tenant_id: str) -> None:
    for cache_key in [key for key in _model_cache if key.startswith(f"{tenant_id}:")]:
        _model_cache.pop(cache_key, None)


def _validate_score_request(req: "ScoreRequest") -> None:
    if not str(req.tenant_id or "").strip():
        raise HTTPException(422, "tenant_id obrigatorio")
    if not str(req.player_id or "").strip():
        raise HTTPException(422, "player_id obrigatorio")


def _handle_missing_champion_model(req: "ScoreRequest") -> "ScoreResponse":
    logger.warning(
        "ml_champion_model_missing",
        tenant_id=req.tenant_id,
        player_id=req.player_id,
        environment=ENVIRONMENT,
    )
    if not _is_non_prod_environment():
        raise HTTPException(503, "Nenhum champion ML ativo para o tenant")
    return ScoreResponse(
        player_id=req.player_id,
        tenant_id=req.tenant_id,
        anomaly_score=0.0,
        is_anomaly=False,
        top_drivers=[],
        model_id=None,
        scored_at=_utcnow().isoformat(),
    )


def _require_synthetic_training_allowed(*, tenant_id: str, endpoint: str, have_rows: int | None = None, minimum_rows: int | None = None) -> None:
    if _allow_synthetic_training():
        return
    logger.warning(
        "synthetic_training_blocked",
        tenant_id=tenant_id,
        endpoint=endpoint,
        environment=ENVIRONMENT,
        have_rows=have_rows,
        minimum_rows=minimum_rows,
    )
    raise HTTPException(409, "Treino sintético desabilitado neste ambiente; dados reais insuficientes")


def _stable_bucket_0_99(tenant_id: str, player_id: str) -> int:
    key = f"{tenant_id}:{player_id}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=False) % 100


def _choose_model_variant(tenant_id: str, player_id: str, challenger_pct: int) -> str:
    pct = max(0, min(int(challenger_pct or 0), 100))
    if pct <= 0:
        return "champion"
    if pct >= 100:
        return "challenger"
    return "challenger" if _stable_bucket_0_99(tenant_id, player_id) < pct else "champion"


def _parse_uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return None


def _get_ml_challenger_pct(engine, tenant_id: str) -> int:
    import sqlalchemy as sa
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant_id},
            )
            row = conn.execute(
                sa.text(
                    "SELECT ml_challenger_pct FROM scoring_configs "
                    "WHERE tenant_id = :tid LIMIT 1"
                ),
                {"tid": tenant_id},
            ).fetchone()
        if row is None:
            return 0
        pct = int(row[0])
        return max(0, min(pct, 100))
    except Exception as exc:
        logger.warning("scoring_config_fetch_failed", tenant_id=tenant_id, error=str(exc))
        return 0


def _log_inference(
    engine,
    *,
    tenant_id: str,
    player_id: str,
    model_id: str | None,
    model_variant: str,
    anomaly_score: float,
    is_anomaly: bool,
    request_id: str | None,
) -> None:
    import sqlalchemy as sa

    tid = _parse_uuid_or_none(tenant_id)
    if tid is None:
        return

    pid = _parse_uuid_or_none(player_id)
    mid = _parse_uuid_or_none(model_id)

    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tid},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO model_inference_logs "
                    "(tenant_id, player_id, model_id, model_variant, anomaly_score, is_anomaly, request_id) "
                    "VALUES (:tenant_id, :player_id, :model_id, :model_variant, :anomaly_score, :is_anomaly, :request_id)"
                ),
                {
                    "tenant_id": tid,
                    "player_id": pid,
                    "model_id": mid,
                    "model_variant": model_variant,
                    "anomaly_score": float(anomaly_score),
                    "is_anomaly": bool(is_anomaly),
                    "request_id": request_id,
                },
            )
    except Exception as exc:
        logger.warning(
            "inference_log_failed",
            tenant_id=tenant_id,
            player_id=player_id,
            model_id=model_id,
            error=str(exc),
        )

# ──────────────────────────────────────────────────────────────────────────────
# Helpers MinIO
# ──────────────────────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    endpoint = MINIO_ENDPOINT.replace("http://", "").replace("https://", "")
    secure = MINIO_ENDPOINT.startswith("https://")
    return Minio(
        endpoint,
        access_key=MINIO_ACCESS,
        secret_key=MINIO_SECRET,
        secure=secure,
    )


def _ensure_bucket(client):
    if not client.bucket_exists(BUCKET_MODELS):
        client.make_bucket(BUCKET_MODELS)


def upload_model_artifact(tenant_id: str, model_id: str, clf) -> str:
    """Serializa e faz upload do modelo para MinIO."""
    client = _minio_client()
    _ensure_bucket(client)
    buf = io.BytesIO()
    joblib.dump(clf, buf)
    size = buf.tell()
    buf.seek(0)
    object_name = f"{tenant_id}/{model_id}.pkl"
    client.put_object(BUCKET_MODELS, object_name, buf, size)
    return object_name


def download_model_artifact(object_name: str):
    """Baixa e desserializa o modelo do MinIO."""
    client = _minio_client()
    response = client.get_object(BUCKET_MODELS, object_name)
    data = response.read()
    response.close()
    response.release_conn()
    return joblib.load(io.BytesIO(data))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers Postgres (sync — ml_service usa operações pontuais)
# ──────────────────────────────────────────────────────────────────────────────

def _db_engine():
    import sqlalchemy as sa
    sync_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(sync_url, pool_pre_ping=True)


def register_model_db(
    engine,
    tenant_id: str,
    model_id: str,
    artifact_uri: str,
    algorithm: str,
    metrics: dict,
    feature_columns: list[str],
    trained_by: str = "ml-service",
) -> None:
    import sqlalchemy as sa
    with engine.begin() as conn:
        # Desativa versões anteriores do mesmo tenant
        conn.execute(
            sa.text(
                "UPDATE model_registry SET is_active = false "
                "WHERE tenant_id = :tid AND is_active = true"
            ),
            {"tid": tenant_id},
        )
        conn.execute(sa.text("""
            INSERT INTO model_registry
                (id, tenant_id, algorithm, artifact_uri, is_active, trained_at,
                 training_rows, metrics, feature_columns, trained_by)
            VALUES
                (:id, :tid, :algo, :uri, true, :ts, :rows, :metrics, :fc, :by)
        """), {
            "id":    model_id,
            "tid":   tenant_id,
            "algo":  algorithm,
            "uri":   artifact_uri,
            "ts":    _utcnow(),
            "rows":  int(metrics.get("training_rows", 0)),
            "metrics": json.dumps(metrics),
            "fc":    json.dumps(feature_columns),
            "by":    trained_by,
        })


def latest_model_db(engine, tenant_id: str) -> dict | None:
    import sqlalchemy as sa
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT id, artifact_uri, algorithm, feature_columns "
            "FROM model_registry WHERE tenant_id = :tid AND is_active = true "
            "ORDER BY trained_at DESC LIMIT 1"
        ), {"tid": tenant_id}).fetchone()
    if row:
        return dict(row._mapping)
    return None


def all_models_db(engine, tenant_id: str) -> list[dict]:
    import sqlalchemy as sa
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT id, algorithm, artifact_uri, is_active, trained_at, "
            "training_rows, metrics FROM model_registry "
            "WHERE tenant_id = :tid ORDER BY trained_at DESC LIMIT 20"
        ), {"tid": tenant_id}).fetchall()
    return [dict(r._mapping) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Model loading (on-demand, per tenant)
# ──────────────────────────────────────────────────────────────────────────────

def _load_tenant_model_legacy(tenant_id: str) -> dict | None:
    """Legacy wrapper — delegates to new multi-type loader."""
    return _load_tenant_model(tenant_id, model_type="champion")


def _normalize_feature_aliases(features: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(features)
    for alias, canonical in FEATURE_ALIASES.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
    return normalized


def _features_to_vector(features: dict, columns: list[str]) -> np.ndarray:
    normalized = _normalize_feature_aliases(features)
    vec = []
    for col in columns:
        raw = normalized.get(col, 0.0)
        try:
            vec.append(float(raw))
        except (ValueError, TypeError):
            vec.append(0.0)
    return np.array(vec, dtype=np.float32).reshape(1, -1)


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_opentelemetry_stub("ml-service")
    logger.info("ml_service_starting")

    # ── APScheduler: re-training automático diário ────────────────────────────
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore
    from apscheduler.triggers.cron import CronTrigger  # type: ignore
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy import text as _text

    _sched_url = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    _sched_engine = create_async_engine(_sched_url, echo=False, pool_size=2, max_overflow=0)
    _sched_session = async_sessionmaker(_sched_engine, expire_on_commit=False)

    async def _auto_retrain():
        """
        Dispara re-training de IsolationForest para todos os tenants ativos.
        Executado diariamente às 03:00 UTC (hora de baixo tráfego).
        Ignora tenants com < min_rows amostras (padrão 500).
        """
        import asyncio as _asyncio
        logger.info("scheduler_retrain_start")
        try:
            async with _sched_session() as db:
                rows = await db.execute(_text("SELECT id FROM tenants WHERE active = true"))
                tenant_ids = [str(r[0]) for r in rows.fetchall()]

            loop = _asyncio.get_event_loop()
            for tid in tenant_ids:
                try:
                    req = TrainRequest(tenant_id=tid, min_rows=500)
                    # train() é síncrono (CPU-bound) — rodar em thread pool
                    result = await loop.run_in_executor(None, train, req)
                    logger.info(
                        "scheduler_retrain_ok",
                        tenant_id=tid,
                        model_id=getattr(result, "model_id", None),
                        rows=getattr(result, "training_rows", None),
                    )
                except Exception as exc:
                    logger.warning("scheduler_retrain_tenant_failed", tenant_id=tid, error=str(exc))
        except Exception as exc:
            logger.error("scheduler_retrain_failed", error=str(exc))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _auto_retrain,
        trigger=CronTrigger(hour=3, minute=0),   # 03:00 UTC diário
        id="daily_retrain",
        replace_existing=True,
        misfire_grace_time=3600,                  # tolera 1h de atraso
    )
    scheduler.start()
    logger.info("scheduler_started", job="daily_retrain", cron="0 3 * * *")

    yield

    scheduler.shutdown(wait=False)
    await _sched_engine.dispose()
    logger.info("ml_service_stopped")


app = FastAPI(title="BetAML ML Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def bind_trace_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Request-Id")
    event_id = request.headers.get("X-Event-ID") or request.headers.get("X-Event-Id")
    structlog.contextvars.clear_contextvars()
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    if event_id:
        structlog.contextvars.bind_contextvars(event_id=event_id)
    response: Response = await call_next(request)
    if request_id:
        response.headers["X-Request-ID"] = request_id
    if event_id:
        response.headers["X-Event-ID"] = event_id
    return response


Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_group_untemplated=True,
    excluded_handlers=["/metrics", "/docs", "/openapi.json"],
).instrument(app).expose(app, include_in_schema=False, tags=["observability"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    player_id: str
    tenant_id: str
    features: dict[str, Any] = Field(default_factory=dict)

    model_config = {"protected_namespaces": ()}


class ScoreResponse(BaseModel):
    player_id: str
    tenant_id: str
    anomaly_score: float          # 0..1 (normalizado)
    is_anomaly: bool
    top_drivers: list[str]
    model_id: str | None = None
    scored_at: str

    model_config = {"protected_namespaces": ()}


class TrainRequest(BaseModel):
    tenant_id: str
    min_rows:  int = 500

    model_config = {"protected_namespaces": ()}


class TrainResponse(BaseModel):
    model_id:      str
    tenant_id:     str
    algorithm:     str
    training_rows: int
    metrics:       dict

    model_config = {"protected_namespaces": ()}


class ModelInfo(BaseModel):
    id:           str
    algorithm:    str
    artifact_uri: str
    is_active:    bool
    trained_at:   str | None
    training_rows: int | None
    metrics:      dict | None

    model_config = {"protected_namespaces": ()}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "ml-service"}


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest, x_request_id: str | None = Header(None, alias="X-Request-Id")):
    """
    Online scoring: recebe features do player e retorna anomaly_score [0,1].
    Sem modelo treinado → retorna 0.0.
    """
    _validate_score_request(req)
    normalized_features = _normalize_feature_aliases(req.features)

    engine = _db_engine()
    challenger_pct = _get_ml_challenger_pct(engine, req.tenant_id)
    preferred_variant = _choose_model_variant(req.tenant_id, req.player_id, challenger_pct)

    entry = _load_tenant_model(req.tenant_id, model_type=preferred_variant)
    chosen_variant = preferred_variant
    if entry is None and preferred_variant == "challenger":
        entry = _load_tenant_model(req.tenant_id, model_type="champion")
        chosen_variant = "champion" if entry is not None else preferred_variant

    if entry is None:
        return _handle_missing_champion_model(req)

    clf = entry["clf"]
    fc  = entry.get("feature_columns", FEATURE_COLS)
    X   = _features_to_vector(normalized_features, fc)

    try:
        # IsolationForest: score < 0 → anomalia; decision_function → [-1, 1]
        raw_score = clf.decision_function(X)[0]  # mais negativo = mais anômalo
        # Normaliza para [0,1]: 0 = normal, 1 = máxima anomalia
        anomaly_score = float(np.clip((raw_score * -1 + 1) / 2, 0.0, 1.0))
        is_anomaly = anomaly_score >= 0.65
    except Exception as exc:  # noqa: BLE001
        ML_SCORING_FAILURES.labels(tenant_id=req.tenant_id, reason=exc.__class__.__name__).inc()
        logger.warning("ml_score_failed", tenant_id=req.tenant_id, player_id=req.player_id, error=str(exc))
        raise HTTPException(503, "Falha temporária no scoring ML") from exc

    # Top drivers: features com maior desvio em relação a zero (proxy simples)
    drivers = sorted(
        [(col, abs(float(normalized_features.get(col, 0) or 0))) for col in fc],
        key=lambda t: t[1],
        reverse=True,
    )[:5]
    top_drivers = [col for col, _ in drivers if _ > 0]

    try:
        _log_inference(
            engine,
            tenant_id=req.tenant_id,
            player_id=req.player_id,
            model_id=entry.get("model_id"),
            model_variant=chosen_variant,
            anomaly_score=round(anomaly_score, 4),
            is_anomaly=is_anomaly,
            request_id=x_request_id,
        )
    except Exception:
        # best-effort logging
        pass

    return ScoreResponse(
        player_id=req.player_id,
        tenant_id=req.tenant_id,
        anomaly_score=round(anomaly_score, 4),
        is_anomaly=is_anomaly,
        top_drivers=top_drivers,
        model_id=entry.get("model_id"),
        scored_at=_utcnow().isoformat(),
    )


@app.post("/train", response_model=TrainResponse)
def train(req: TrainRequest):
    """
    Trigger training: carrega features do ClickHouse/Redis e treina IsolationForest.
    Para dev/MVP: usa dados sintéticos quando não há dados reais suficientes.
    """
    from sklearn.ensemble import IsolationForest

    tenant_id = req.tenant_id
    rows: list[dict] = []

    # Tentativa 1: ClickHouse (Gold)
    try:
        import clickhouse_driver  # noqa: F401
        CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
        client = clickhouse_driver.Client(host=CLICKHOUSE_HOST, port=9000)
        raw = client.execute(
            "SELECT * FROM betaml.player_features_daily "
            "WHERE tenant_id = %(tid)s LIMIT 10000",
            {"tid": tenant_id},
        )
        if raw and len(raw) > 0:
            cols = [c[0] for c in client.execute(
                "DESCRIBE TABLE betaml.player_features_daily"
            )]
            rows = [dict(zip(cols, row)) for row in raw]
    except Exception as e:
        logger.warning("clickhouse_train_load_failed", error=str(e))

    synthetic_bootstrap = False
    if len(rows) < req.min_rows:
        _require_synthetic_training_allowed(
            tenant_id=tenant_id,
            endpoint="/train",
            have_rows=len(rows),
            minimum_rows=req.min_rows,
        )
        # Gera dados sintéticos para bootstrap (dev)
        logger.info("synthetic_data_generation", tenant_id=tenant_id, have=len(rows))
        rng = np.random.default_rng(42)
        n_synth = max(req.min_rows, 1000)
        synth = {col: rng.exponential(100, n_synth).tolist() for col in FEATURE_COLS}
        synth["zscore_current_deposit_vs_baseline"] = rng.normal(0, 1, n_synth).tolist()
        synth["new_payment_instrument_flag"] = rng.integers(0, 2, n_synth).tolist()
        rows = [
            {col: float(synth[col][i]) for col in FEATURE_COLS}
            for i in range(n_synth)
        ]
        synthetic_bootstrap = True

    # Monta matriz X
    X_list = []
    for row in rows:
        normalized_row = _normalize_feature_aliases(row)
        vec = [float(normalized_row.get(col, 0.0) or 0.0) for col in FEATURE_COLS]
        X_list.append(vec)
    X = np.array(X_list, dtype=np.float32)

    # Treina modelo
    clf = IsolationForest(
        n_estimators=200,
        contamination=0.05,  # assume 5% de anomalias
        max_features=min(len(FEATURE_COLS), 10),
        random_state=42,
        n_jobs=-1,
    )
    t0 = time.time()
    clf.fit(X)
    train_secs = round(time.time() - t0, 2)

    model_id = str(uuid.uuid4())
    metrics = {
        "training_rows": len(rows),
        "n_estimators":  200,
        "contamination": 0.05,
        "train_secs":    train_secs,
        "synthetic_bootstrap": synthetic_bootstrap,
    }

    # Upload para MinIO
    try:
        artifact_uri = upload_model_artifact(tenant_id, model_id, clf)
    except Exception as e:
        logger.error("minio_upload_failed", error=str(e))
        artifact_uri = f"memory://{tenant_id}/{model_id}.pkl"

    # Registra no Postgres
    try:
        engine = _db_engine()
        register_model_db(
            engine, tenant_id, model_id, artifact_uri,
            "IsolationForest", metrics, FEATURE_COLS
        )
    except Exception as e:
        logger.error("model_register_failed", error=str(e))

    # Invalida cache para forçar reload
    _clear_tenant_model_cache(tenant_id)

    logger.info("model_trained", tenant_id=tenant_id, model_id=model_id, rows=len(rows))
    return TrainResponse(
        model_id=model_id,
        tenant_id=tenant_id,
        algorithm="IsolationForest",
        training_rows=len(rows),
        metrics=metrics,
    )


@app.get("/models")
def list_models(x_tenant_id: str = Header(..., alias="X-Tenant-Id")) -> list[ModelInfo]:
    try:
        engine = _db_engine()
        rows = all_models_db(engine, tenant_id=x_tenant_id)
        return [
            ModelInfo(
                id=r["id"],
                algorithm=r.get("algorithm", ""),
                artifact_uri=r.get("artifact_uri", ""),
                is_active=bool(r.get("is_active", False)),
                trained_at=str(r["trained_at"]) if r.get("trained_at") else None,
                training_rows=r.get("training_rows"),
                metrics=json.loads(r["metrics"]) if isinstance(r.get("metrics"), str) else r.get("metrics"),
            )
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/models/reload")
def reload_model(x_tenant_id: str = Header(..., alias="X-Tenant-Id")):
    """Invalida cache de modelo para forçar reload na próxima requisição."""
    _clear_tenant_model_cache(x_tenant_id)
    return {"status": "cache_cleared", "tenant_id": x_tenant_id}


# ──────────────────────────────────────────────────────────────────────────────
# M4 — SHAP explainability
# ──────────────────────────────────────────────────────────────────────────────

class SHAPRequest(BaseModel):
    tenant_id: str
    player_id: str
    features:  dict[str, Any]
    model_type: str = "IsolationForest"

    model_config = {"protected_namespaces": ()}


class SHAPResponse(BaseModel):
    player_id:    str
    tenant_id:    str
    model_type:   str
    shap_values:  dict[str, float]
    baseline:     float
    scored_at:    str

    model_config = {"protected_namespaces": ()}


@app.post("/score/shap", response_model=SHAPResponse)
def score_shap(req: SHAPRequest):
    """
    Returns per-feature SHAP-style importance for an anomaly score.
    Uses a permutation-based approximation when SHAP library is available,
    otherwise falls back to gradient/mean-deviation proxy.
    """
    entry = _load_tenant_model(req.tenant_id)
    if entry is None:
        raise HTTPException(404, "No model found for tenant")

    clf = entry["clf"]
    fc  = entry.get("feature_columns", FEATURE_COLS)
    X   = _features_to_vector(_normalize_feature_aliases(req.features), fc)

    def _score_vec(x: np.ndarray) -> float:
        raw = clf.decision_function(x.reshape(1, -1))[0]
        return float(np.clip((raw * -1 + 1) / 2, 0.0, 1.0))

    base_score = _score_vec(X)
    shap_vals: dict[str, float] = {}

    try:
        import shap  # type: ignore
        explainer   = shap.TreeExplainer(clf)
        shap_array  = explainer.shap_values(X)
        for i, col in enumerate(fc):
            shap_vals[col] = float(shap_array[0][i])
    except Exception:
        # Permutation-based fallback
        for i, col in enumerate(fc):
            x_perm = X.copy()
            x_perm[i] = 0.0          # zero-out feature
            permuted_score = _score_vec(x_perm)
            shap_vals[col] = round(base_score - permuted_score, 5)

    return SHAPResponse(
        player_id=req.player_id,
        tenant_id=req.tenant_id,
        model_type=req.model_type,
        shap_values=shap_vals,
        baseline=round(base_score, 4),
        scored_at=_utcnow().isoformat(),
    )


# ──────────────────────────────────────────────────────────────────────────────
# M4 — A/B testing: champion + challenger scoring
# ──────────────────────────────────────────────────────────────────────────────

class ABScoreResponse(BaseModel):
    player_id:           str
    tenant_id:           str
    champion_score:      Optional[float] = None
    challenger_score:    Optional[float] = None
    champion_model_id:   Optional[str]   = None
    challenger_model_id: Optional[str]   = None
    delta:               Optional[float] = None
    scored_at:           str


@app.post("/score/ab", response_model=ABScoreResponse)
def score_ab(req: ScoreRequest):
    """
    Score against both champion and challenger models (A/B test).
    Returns deltas for comparison.
    """
    _validate_score_request(req)

    def _score_entry(tenant_id: str, model_type: str) -> tuple[float, str | None]:
        entry = _load_tenant_model(tenant_id, model_type=model_type)
        if entry is None:
            return 0.0, None
        clf = entry["clf"]
        fc  = entry.get("feature_columns", FEATURE_COLS)
        X   = _features_to_vector(_normalize_feature_aliases(req.features), fc)
        raw = clf.decision_function(X)[0]
        return float(np.clip((raw * -1 + 1) / 2, 0.0, 1.0)), entry.get("model_id")

    champ_score, champ_id    = _score_entry(req.tenant_id, "champion")
    challenger_score, chal_id = _score_entry(req.tenant_id, "challenger")

    if champ_id is None and not _is_non_prod_environment():
        raise HTTPException(503, "Nenhum champion ML ativo para comparacao A/B")

    delta = round(challenger_score - champ_score, 4) if chal_id else None
    return ABScoreResponse(
        player_id=req.player_id,
        tenant_id=req.tenant_id,
        champion_score=round(champ_score, 4),
        challenger_score=round(challenger_score, 4) if chal_id else None,
        champion_model_id=champ_id,
        challenger_model_id=chal_id,
        delta=delta,
        scored_at=_utcnow().isoformat(),
    )


@app.get("/models/{model_id}/ab-metrics")
def ab_metrics(
    model_id: str,
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
    days: int = Query(7, le=90),
):
    """
    Return aggregated A/B test metrics for a challenger model vs champion.
    Pulls from model_registry metrics column.
    """
    try:
        engine = _db_engine()
        import sqlalchemy as sa
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT id, algorithm, metrics, is_challenger, champion_id "
                    "FROM model_registry WHERE id = :mid AND tenant_id = :tid"
                ),
                {"mid": model_id, "tid": x_tenant_id},
            ).fetchone()
        if row is None:
            raise HTTPException(404)
        metrics = json.loads(row.metrics) if isinstance(row.metrics, str) else (row.metrics or {})
        return {
            "model_id":     model_id,
            "is_challenger": bool(row.is_challenger),
            "champion_id":  row.champion_id,
            "metrics":      metrics,
            "days_window":  days,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e)) from e


# ──────────────────────────────────────────────────────────────────────────────
# M4 — StructuringDetector: supervised binary classifier
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/train/structuring")
def train_structuring(req: TrainRequest):
    """
    Train a supervised binary classifier (StructuringDetector) using labeled
    True Positive alerts as positive class and normal transactions as negative.
    Falls back to synthetic data in dev.
    """
    from sklearn.ensemble import GradientBoostingClassifier

    _require_synthetic_training_allowed(tenant_id=req.tenant_id, endpoint="/train/structuring")

    tenant_id = req.tenant_id
    X_pos_list: list[list[float]] = []
    X_neg_list: list[list[float]] = []

    # Fetch labeled TPs from Postgres
    try:
        engine = _db_engine()
        import sqlalchemy as sa
        with engine.connect() as conn:
            labeled = conn.execute(
                sa.text("""
                    SELECT a.player_id, a.evidence
                    FROM alerts a
                    WHERE a.tenant_id = :tid AND a.label = 'TRUE_POSITIVE'
                    ORDER BY a.created_at DESC LIMIT 5000
                """),
                {"tid": tenant_id},
            ).fetchall()
        for row in labeled:
            ev = json.loads(row.evidence) if isinstance(row.evidence, str) else (row.evidence or {})
            fs = _normalize_feature_aliases(ev.get("feature_snapshot", {}))
            vec = [float(fs.get(col, 0.0) or 0.0) for col in STRUCTURING_COLS]
            X_pos_list.append(vec)
    except Exception as e:
        logger.warning("structuring_labeled_load_failed", error=str(e))

    # Synthetic bootstrap if not enough data
    rng = np.random.default_rng(42)
    n_synth = max(req.min_rows, 500)
    if len(X_pos_list) < 50:
        logger.info("structuring_synthetic_positives", n=n_synth // 5)
        for _ in range(n_synth // 5):
            vec = [float(rng.exponential(5000)) if "sum" in col or "velocity" in col
                   else float(rng.uniform(0.5, 1.0))
                   for col in STRUCTURING_COLS]
            X_pos_list.append(vec)

    for _ in range(n_synth):
        vec = [float(rng.exponential(100)) for _ in STRUCTURING_COLS]
        X_neg_list.append(vec)

    X = np.array(X_pos_list + X_neg_list, dtype=np.float32)
    y = np.array([1] * len(X_pos_list) + [0] * len(X_neg_list))

    clf = GradientBoostingClassifier(n_estimators=150, max_depth=4, learning_rate=0.1,
                                     random_state=42)
    t0 = time.time()
    clf.fit(X, y)
    train_secs = round(time.time() - t0, 2)

    model_id = str(uuid.uuid4())
    metrics = {
        "training_rows": len(X),
        "positives":     len(X_pos_list),
        "negatives":     len(X_neg_list),
        "train_secs":    train_secs,
        "algorithm":     "GradientBoosting_StructuringDetector",
        "synthetic_bootstrap": True,
    }

    try:
        artifact_uri = upload_model_artifact(tenant_id, model_id, clf)
    except Exception as e:
        logger.error("minio_upload_failed", error=str(e))
        artifact_uri = f"memory://{tenant_id}/{model_id}.pkl"

    try:
        engine = _db_engine()
        register_model_db(engine, tenant_id, model_id, artifact_uri,
                          "StructuringDetector", metrics, STRUCTURING_COLS)
    except Exception as e:
        logger.error("model_register_failed", error=str(e))

    _clear_tenant_model_cache(tenant_id)

    return TrainResponse(model_id=model_id, tenant_id=tenant_id,
                         algorithm="StructuringDetector",
                         training_rows=len(X), metrics=metrics)


# ──────────────────────────────────────────────────────────────────────────────
# M4 — GraphClustering: DBSCAN for shared-device/instrument clusters
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/train/graph")
def train_graph(req: TrainRequest):
    """
    Train DBSCAN graph clustering model.
    Each player maps to a cluster_id; isolated/dense clusters indicate networks.
    """
    from sklearn.cluster import DBSCAN
    from sklearn.preprocessing import StandardScaler

    _require_synthetic_training_allowed(tenant_id=req.tenant_id, endpoint="/train/graph")

    tenant_id = req.tenant_id
    rng = np.random.default_rng(42)
    n = max(req.min_rows, 500)

    # Synthetic: simulate player feature matrix
    X = np.column_stack([
        rng.integers(0, 20, n).astype(float),  # shared_device_count
        rng.random(n),                           # shared_instrument_score
        rng.integers(1, 10, n).astype(float),   # unique_instruments_7d
        rng.integers(0, 2, n).astype(float),    # inconsistent_currency_flag
    ])
    X = StandardScaler().fit_transform(X)

    clf = DBSCAN(eps=0.5, min_samples=3, metric="euclidean", n_jobs=-1)
    labels = clf.fit_predict(X)

    n_clusters  = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise     = int((labels == -1).sum())
    model_id    = str(uuid.uuid4())
    metrics     = {
        "n_clusters": n_clusters,
        "n_noise":    n_noise,
        "eps":        0.5,
        "min_samples": 3,
        "algorithm":  "DBSCAN_GraphClustering",
        "synthetic_bootstrap": True,
    }

    try:
        artifact_uri = upload_model_artifact(tenant_id, model_id, clf)
    except Exception as e:
        artifact_uri = f"memory://{tenant_id}/{model_id}.pkl"

    try:
        engine = _db_engine()
        register_model_db(engine, tenant_id, model_id, artifact_uri,
                          "GraphClustering", metrics, GRAPH_COLS)
    except Exception as e:
        logger.error("graph_register_failed", error=str(e))

    _clear_tenant_model_cache(tenant_id)

    return TrainResponse(model_id=model_id, tenant_id=tenant_id,
                         algorithm="GraphClustering",
                         training_rows=n, metrics=metrics)


# ──────────────────────────────────────────────────────────────────────────────
# M4 — RecurrenceEstimator: device+IP+temporal pattern matching
# ──────────────────────────────────────────────────────────────────────────────

RECURRENCE_COLS = [
    "night_activity_ratio", "weekend_activity_ratio",
    "deposit_velocity", "chargeback_rate_30d",
    "win_loss_ratio_30d", "avg_odds_bet_7d",
    "cashout_ratio_7d",
]


@app.post("/train/recurrence")
def train_recurrence(req: TrainRequest):
    """
    Train a recurrence estimator: identifies players whose behavioural
    temporal patterns match prior high-risk profiles (k-NN in feature space).
    """
    from sklearn.neighbors import NearestNeighbors

    _require_synthetic_training_allowed(tenant_id=req.tenant_id, endpoint="/train/recurrence")

    tenant_id = req.tenant_id
    rng = np.random.default_rng(42)
    n   = max(req.min_rows, 500)

    X = np.column_stack([
        rng.uniform(0, 1, n),   # night_activity_ratio
        rng.uniform(0, 1, n),   # weekend_activity_ratio
        rng.exponential(2, n),  # deposit_velocity
        rng.uniform(0, 0.5, n), # chargeback_rate_30d
        rng.uniform(0, 5, n),   # win_loss_ratio_30d
        rng.exponential(2, n),  # avg_odds_bet_7d
        rng.uniform(0, 3, n),   # cashout_ratio_7d
    ])

    clf = NearestNeighbors(n_neighbors=5, algorithm="ball_tree", metric="euclidean")
    t0 = time.time()
    clf.fit(X)
    train_secs = round(time.time() - t0, 2)

    model_id = str(uuid.uuid4())
    metrics  = {
        "training_rows": n,
        "n_neighbors": 5,
        "algorithm": "NearestNeighbors_RecurrenceEstimator",
        "train_secs": train_secs,
        "synthetic_bootstrap": True,
    }

    try:
        artifact_uri = upload_model_artifact(tenant_id, model_id, clf)
    except Exception as e:
        artifact_uri = f"memory://{tenant_id}/{model_id}.pkl"

    try:
        engine = _db_engine()
        register_model_db(engine, tenant_id, model_id, artifact_uri,
                          "RecurrenceEstimator", metrics, RECURRENCE_COLS)
    except Exception as e:
        logger.error("recurrence_register_failed", error=str(e))

    _clear_tenant_model_cache(tenant_id)

    return TrainResponse(model_id=model_id, tenant_id=tenant_id,
                         algorithm="RecurrenceEstimator",
                         training_rows=n, metrics=metrics)


# ──────────────────────────────────────────────────────────────────────────────
# M4 — Helper: load tenant model by type
# ──────────────────────────────────────────────────────────────────────────────

def _load_tenant_model(tenant_id: str, model_type: str = "champion") -> dict | None:
    """Load model from cache; falls back to Postgres + MinIO."""
    cache_key = f"{tenant_id}:{model_type}"
    if cache_key in _model_cache:
        return _model_cache[cache_key]

    try:
        engine = _db_engine()
        import sqlalchemy as sa
        if model_type == "challenger":
            query = sa.text(
                "SELECT id, artifact_uri, algorithm, feature_columns "
                "FROM model_registry WHERE tenant_id = :tid "
                "AND status = 'challenger' AND is_challenger = true "
                "ORDER BY trained_at DESC LIMIT 1"
            )
        else:
            query = sa.text(
                "SELECT id, artifact_uri, algorithm, feature_columns "
                "FROM model_registry WHERE tenant_id = :tid "
                "AND status IN ('champion', 'active', 'PRODUCTION') "
                "AND COALESCE(is_challenger, false) = false "
                "ORDER BY trained_at DESC LIMIT 1"
            )
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.current_tenant', :tid, true)"),
                {"tid": tenant_id},
            )
            row = conn.execute(query, {"tid": tenant_id}).fetchone()
        if row is None:
            return None
        clf = download_model_artifact(row.artifact_uri)
        fc  = json.loads(row.feature_columns) if isinstance(row.feature_columns, str) else (row.feature_columns or FEATURE_COLS)
        entry = {"clf": clf, "model_id": str(row.id), "algorithm": row.algorithm, "feature_columns": fc}
        _model_cache[cache_key] = entry
        return entry
    except Exception as e:
        logger.warning("model_load_failed", tenant=tenant_id, error=str(e))
        return None
