"""
ML Service — BetAML
REST API: POST /score  (online scoring)
         POST /train  (trigger training job)
         GET  /models (listar model registry)
Modelo: IsolationForest (sklearn) por tenant
Artefatos: MinIO   (betaml-models/{tenant_id}/{model_id}.pkl)
Registro:  Postgres (model_registry)
"""
from __future__ import annotations

import io
import json
import os
import pickle
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import joblib
import numpy as np
import structlog
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

logger = structlog.get_logger()

DATABASE_URL   = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@localhost:5432/betaml_dev")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minio123")
BUCKET_MODELS  = "betaml-models"

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
]

# Cache de modelos carregados por tenant
_model_cache: dict[str, dict[str, Any]] = {}

# ──────────────────────────────────────────────────────────────────────────────
# Helpers MinIO
# ──────────────────────────────────────────────────────────────────────────────

def _minio_client():
    from minio import Minio
    return Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS,
        secret_key=MINIO_SECRET,
        secure=False,
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
            "ts":    datetime.utcnow(),
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

def _load_tenant_model(tenant_id: str) -> dict | None:
    if tenant_id in _model_cache:
        return _model_cache[tenant_id]
    try:
        engine = _db_engine()
        row = latest_model_db(engine, tenant_id)
        if not row:
            return None
        clf = download_model_artifact(row["artifact_uri"])
        fc  = json.loads(row["feature_columns"]) if isinstance(row["feature_columns"], str) else row["feature_columns"]
        entry = {"clf": clf, "feature_columns": fc, "model_id": row["id"], "algorithm": row["algorithm"]}
        _model_cache[tenant_id] = entry
        logger.info("model_loaded", tenant_id=tenant_id, model_id=row["id"])
        return entry
    except Exception as e:
        logger.warning("model_load_failed", tenant_id=tenant_id, error=str(e))
        return None


def _features_to_vector(features: dict, columns: list[str]) -> np.ndarray:
    vec = []
    for col in columns:
        raw = features.get(col, 0.0)
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
    logger.info("ml_service_starting")
    yield
    logger.info("ml_service_stopped")


app = FastAPI(title="BetAML ML Service", version="1.0.0", lifespan=lifespan)


# ── Schemas ──────────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    player_id: str
    tenant_id: str
    features: dict[str, Any] = Field(default_factory=dict)


class ScoreResponse(BaseModel):
    player_id: str
    tenant_id: str
    anomaly_score: float          # 0..1 (normalizado)
    is_anomaly: bool
    top_drivers: list[str]
    model_id: str | None = None
    scored_at: str


class TrainRequest(BaseModel):
    tenant_id: str
    min_rows:  int = 500


class TrainResponse(BaseModel):
    model_id:      str
    tenant_id:     str
    algorithm:     str
    training_rows: int
    metrics:       dict


class ModelInfo(BaseModel):
    id:           str
    algorithm:    str
    artifact_uri: str
    is_active:    bool
    trained_at:   str | None
    training_rows: int | None
    metrics:      dict | None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "ml-service"}


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest):
    """
    Online scoring: recebe features do player e retorna anomaly_score [0,1].
    Sem modelo treinado → retorna 0.0.
    """
    entry = _load_tenant_model(req.tenant_id)
    if entry is None:
        return ScoreResponse(
            player_id=req.player_id,
            tenant_id=req.tenant_id,
            anomaly_score=0.0,
            is_anomaly=False,
            top_drivers=[],
            model_id=None,
            scored_at=datetime.utcnow().isoformat(),
        )

    clf = entry["clf"]
    fc  = entry.get("feature_columns", FEATURE_COLS)
    X   = _features_to_vector(req.features, fc)

    # IsolationForest: score < 0 → anomalia; decision_function → [-1, 1]
    raw_score = clf.decision_function(X)[0]  # mais negativo = mais anômalo
    # Normaliza para [0,1]: 0 = normal, 1 = máxima anomalia
    anomaly_score = float(np.clip((raw_score * -1 + 1) / 2, 0.0, 1.0))
    is_anomaly = anomaly_score >= 0.65

    # Top drivers: features com maior desvio em relação a zero (proxy simples)
    drivers = sorted(
        [(col, abs(float(req.features.get(col, 0)))) for col in fc],
        key=lambda t: t[1],
        reverse=True,
    )[:5]
    top_drivers = [col for col, _ in drivers if _ > 0]

    return ScoreResponse(
        player_id=req.player_id,
        tenant_id=req.tenant_id,
        anomaly_score=round(anomaly_score, 4),
        is_anomaly=is_anomaly,
        top_drivers=top_drivers,
        model_id=entry.get("model_id"),
        scored_at=datetime.utcnow().isoformat(),
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

    if len(rows) < req.min_rows:
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

    # Monta matriz X
    X_list = []
    for row in rows:
        vec = [float(row.get(col, 0.0) or 0.0) for col in FEATURE_COLS]
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
    _model_cache.pop(tenant_id, None)

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
    _model_cache.pop(x_tenant_id, None)
    return {"status": "cache_cleared", "tenant_id": x_tenant_id}
