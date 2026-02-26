"""Batch training logic for IsolationForest anomaly detection."""

import io
import logging
import pickle
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.ensemble import IsolationForest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ml.config import settings
from ml.models import ModelRegistry

logger = logging.getLogger(__name__)

# The 17 numerical features expected from player_features
FEATURE_NAMES: list[str] = [
    "session_count_7d",
    "session_count_30d",
    "avg_session_duration_minutes",
    "total_wagered_7d",
    "total_wagered_30d",
    "total_deposits_7d",
    "total_deposits_30d",
    "deposit_count_7d",
    "withdrawal_count_7d",
    "net_loss_7d",
    "net_loss_30d",
    "games_played_7d",
    "unique_games_7d",
    "late_night_sessions_pct",
    "avg_bet_size",
    "max_bet_size",
    "chasing_loss_score",
]


def _get_minio_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"http://{settings.MINIO_ENDPOINT}",
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _ensure_bucket(client) -> None:
    try:
        client.head_bucket(Bucket=settings.MINIO_BUCKET)
    except Exception:
        client.create_bucket(Bucket=settings.MINIO_BUCKET)


def _load_from_clickhouse(tenant_id: str, window_days: int) -> np.ndarray | None:
    """Return feature matrix or None if ClickHouse is unavailable / empty."""
    try:
        from clickhouse_driver import Client

        ch = Client(
            host=settings.CLICKHOUSE_HOST,
            port=settings.CLICKHOUSE_PORT,
            database=settings.CLICKHOUSE_DB,
            user=settings.CLICKHOUSE_USER,
            password=settings.CLICKHOUSE_PASSWORD,
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        rows = ch.execute(
            "SELECT * FROM player_features WHERE tenant_id = %(tid)s AND created_at > %(cutoff)s",
            {"tid": tenant_id, "cutoff": cutoff},
        )
        if not rows:
            return None
        # Assume columns are ordered; extract the 17 feature columns by name
        col_names = [col[0] for col in ch.execute("DESCRIBE TABLE player_features")]
        feat_indices = [col_names.index(f) for f in FEATURE_NAMES if f in col_names]
        if not feat_indices:
            return None
        return np.array([[row[i] for i in feat_indices] for row in rows], dtype=float)
    except Exception as exc:
        logger.warning("ClickHouse unavailable, will use synthetic data: %s", exc)
        return None


def _synthetic_data(n: int = 50) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.exponential(scale=1.0, size=(n, len(FEATURE_NAMES))).astype(float)


class ModelTrainer:
    async def train(
        self,
        tenant_id: str,
        dataset_window_days: int = 90,
        db: AsyncSession | None = None,
    ) -> dict:
        # 1. Load features
        X = _load_from_clickhouse(tenant_id, dataset_window_days)

        # 2. Fall back to synthetic data
        if X is None or X.shape[0] == 0:
            logger.info("Using synthetic data for tenant %s", tenant_id)
            X = _synthetic_data()

        n_samples, n_features = X.shape

        # 4. Train model
        clf = IsolationForest(
            contamination=settings.IF_CONTAMINATION,
            n_estimators=settings.IF_N_ESTIMATORS,
            random_state=settings.IF_RANDOM_STATE,
        )
        clf.fit(X)

        # 6. Compute metrics
        scores = clf.score_samples(X)
        metrics = {
            "n_samples": int(n_samples),
            "n_features": int(n_features),
            "contamination": settings.IF_CONTAMINATION,
            "anomaly_score_mean": float(np.mean(scores)),
            "anomaly_score_std": float(np.std(scores)),
            "feature_means": [float(v) for v in X.mean(axis=0)],
            "feature_stds": [float(v) for v in X.std(axis=0)],
        }

        # 5. Serialize and upload to MinIO
        model_id = uuid.uuid4()
        version = f"v{int(datetime.now(timezone.utc).timestamp())}"
        artifact_path = (
            f"{settings.MODEL_ARTIFACTS_PREFIX}/{tenant_id}/{version}/model.pkl"
        )

        pkl_bytes = pickle.dumps(clf)
        try:
            s3 = _get_minio_client()
            _ensure_bucket(s3)
            s3.put_object(
                Bucket=settings.MINIO_BUCKET,
                Key=artifact_path,
                Body=io.BytesIO(pkl_bytes),
                ContentLength=len(pkl_bytes),
            )
        except Exception as exc:
            logger.warning("MinIO upload failed: %s", exc)

        # 7. Persist to Postgres
        if db is not None:
            tenant_uuid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id

            # Deactivate previous active models for this tenant
            await db.execute(
                update(ModelRegistry)
                .where(ModelRegistry.tenant_id == tenant_uuid, ModelRegistry.is_active.is_(True))
                .values(is_active=False)
            )

            entry = ModelRegistry(
                id=model_id,
                tenant_id=tenant_uuid,
                model_version=version,
                algorithm="IsolationForest",
                trained_at=datetime.now(timezone.utc),
                dataset_window_days=dataset_window_days,
                artifact_path=artifact_path,
                metrics=metrics,
                feature_names=FEATURE_NAMES[:n_features],
                is_active=True,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            model_id = entry.id

        return {
            "model_id": str(model_id),
            "version": version,
            "metrics": metrics,
            "artifact_path": artifact_path,
        }
