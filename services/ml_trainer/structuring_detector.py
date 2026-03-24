"""
structuring_detector — Modelo supervisionado para detecção de depósitos fracionados (structuring).

COMPLIANCE: COAF Res. 36/2021 Art. 6º — Fracionamento de operações (estruturação)
como técnica para evitar identificação de operação suspeita.

Features focadas:
- deposit_count_24h, deposit_count_7d: volume de depósitos
- deposit_velocity: taxa de aceleração
- unique_instruments_used_7d: troca frequente de métodos
- avg_time_between_deposit_and_withdrawal_7d: saque rápido após depósitos múltiplos
- deposit_sum_24h, deposit_sum_7d: valores totais
- night_activity_ratio: operações noturnas (evasão de monitoramento)
- round_amount_ratio: valores redondos suspeitos
- structuring_score: score heurístico pré-calculado (se disponível)

Algoritmo: RandomForestClassifier (robusto a overfitting, interpretável via feature importance)
Target: Alerts com label TRUE_POSITIVE E alert_type contendo "STRUCTURING" ou "FRACIONAMENTO"
"""
from __future__ import annotations

import io
import pickle
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from minio import Minio
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

# 10 features focadas em comportamento de estruturação
STRUCTURING_FEATURES = [
    "deposit_count_24h",
    "deposit_count_7d",
    "deposit_velocity",
    "unique_instruments_used_7d",
    "avg_time_between_deposit_and_withdrawal_7d",
    "deposit_sum_24h",
    "deposit_sum_7d",
    "night_activity_ratio",
    "round_amount_ratio",
    "structuring_score",
]


def _extract_structuring_vector(alert) -> list[float] | None:
    """Extrai vetor de features de estruturação do alert evidence."""
    features = (alert.evidence or {}).get("features", {})
    if not features:
        return None

    # Fallback para features ausentes
    vector = []
    for col in STRUCTURING_FEATURES:
        val = features.get(col)
        if val is None:
            # Heurística: se structuring_score ausente, calcular proxy
            if col == "structuring_score":
                dep_count = features.get("deposit_count_24h", 0) or 0
                dep_vel = features.get("deposit_velocity", 0) or 0
                val = min(1.0, (dep_count / 10.0) * (1 + dep_vel))
            # Outros defaults
            elif col == "round_amount_ratio":
                val = 0.0
            elif col in ["night_activity_ratio", "deposit_velocity"]:
                val = 0.0
            elif col.startswith("deposit_"):
                val = 0.0
            elif col == "avg_time_between_deposit_and_withdrawal_7d":
                val = 48.0  # baseline: 2 dias
            elif col == "unique_instruments_used_7d":
                val = 1.0
            else:
                val = 0.0
        vector.append(float(val))

    return vector


async def train_structuring_detector(
    db: AsyncSession,
    minio_client: Minio,
    bucket: str = "betaml-models",
) -> dict | None:
    """
    Treina RandomForestClassifier para detectar estruturação.

    Returns:
        dict com metrics + artifact_uri + metadata OU None se insuficientes amostras
    """
    from models import Alert  # noqa: PLC0415

    cutoff = datetime.now(UTC) - timedelta(days=60)  # janela maior: 60 dias
    version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    logger.info("structuring_detector_training_start", cutoff=cutoff.isoformat())

    # Busca alerts labelados com foco em structuring
    stmt = select(Alert).where(
        Alert.label.in_(["TRUE_POSITIVE", "FALSE_POSITIVE"]),
        Alert.created_at >= cutoff,
    )
    alerts = (await db.execute(stmt)).scalars().all()

    # Filtra alerts relacionados a structuring (TRUE_POSITIVE) e outros (FALSE_POSITIVE)
    # Prioriza alerts com "STRUCTURING" ou "FRACIONAMENTO" no título/tipo
    X: list[list[float]] = []
    y: list[int] = []

    for alert in alerts:
        vec = _extract_structuring_vector(alert)
        if vec is None:
            continue

        # Target: TRUE_POSITIVE com indícios de structuring = 1, resto = 0
        is_structuring_related = any(
            keyword in (alert.title or "").upper()
            for keyword in ["STRUCTURING", "ESTRUTURA", "FRACION", "MÚLTIPLOS DEPÓSITOS", "SPIKE"]
        )

        if alert.label == "TRUE_POSITIVE" and is_structuring_related:
            label = 1
        elif alert.label == "FALSE_POSITIVE":
            label = 0
        else:
            # TRUE_POSITIVE mas não relacionado a structuring: skip (não é nosso target)
            continue

        X.append(vec)
        y.append(label)

    sample_count = len(X)

    if sample_count < 30:
        logger.warning(
            "structuring_detector_skipped_insufficient_samples",
            samples=sample_count,
            minimum=30,
        )
        return None

    logger.info(
        "structuring_detector_samples",
        total=sample_count,
        positives=sum(y),
        negatives=sample_count - sum(y),
    )

    # ── Treino ──
    X_arr = np.array(X, dtype=float)
    y_arr = np.array(y, dtype=int)

    model = RandomForestClassifier(
        n_estimators=150,
        max_depth=6,
        min_samples_split=5,
        class_weight="balanced",  # compensar desbalanceamento
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_arr, y_arr)

    # ── Métricas in-sample ──
    preds_bin = model.predict(X_arr)
    preds_prob = model.predict_proba(X_arr)[:, 1]

    precision = float(precision_score(y_arr, preds_bin, zero_division=0))
    recall = float(recall_score(y_arr, preds_bin, zero_division=0))
    f1 = float(f1_score(y_arr, preds_bin, zero_division=0))

    try:
        auc_roc = float(roc_auc_score(y_arr, preds_prob))
    except ValueError:
        auc_roc = 0.0

    logger.info(
        "structuring_detector_metrics",
        precision=precision,
        recall=recall,
        f1=f1,
        auc_roc=auc_roc,
    )

    # ── Feature importance (interpretabilidade) ──
    feature_importance = {
        feat: float(imp)
        for feat, imp in zip(STRUCTURING_FEATURES, model.feature_importances_, strict=False)
    }
    top_features = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)[:5]
    logger.info("structuring_detector_top_features", features=dict(top_features))

    # ── Persistência MinIO ──
    model_filename = f"structuring_detector_v{version}.pkl"
    model_bytes = pickle.dumps(model)

    minio_client.put_object(
        bucket,
        model_filename,
        io.BytesIO(model_bytes),
        len(model_bytes),
        content_type="application/octet-stream",
    )

    artifact_uri = f"s3://{bucket}/{model_filename}"
    logger.info("structuring_detector_saved", artifact_uri=artifact_uri)

    return {
        "model_name": "structuring_detector",
        "model_type": "structuring_detection",
        "algorithm": "RandomForestClassifier",
        "model_version": version,
        "artifact_uri": artifact_uri,
        "training_rows": sample_count,
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "auc_roc": auc_roc,
        },
        "feature_columns": STRUCTURING_FEATURES,
        "feature_importance": feature_importance,
        "training_metadata": {
            "n_estimators": 150,
            "max_depth": 6,
            "class_weight": "balanced",
            "training_window_days": 60,
            "positives": int(y_arr.sum()),
            "negatives": int((y_arr == 0).sum()),
        },
    }
