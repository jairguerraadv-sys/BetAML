"""Online scoring logic using cached IsolationForest models."""

import io
import logging
import pickle
import uuid
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ml.config import settings
from ml.models import ModelRegistry
from ml.trainer import FEATURE_NAMES

logger = logging.getLogger(__name__)


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


def _normalize_score(raw: float) -> float:
    """Map IsolationForest score_samples output to [0, 1] anomaly score.

    score_samples returns negative values; more negative → more anomalous.
    We clip to [-1, 0] then invert so 1 = most anomalous.
    """
    clipped = max(-1.0, min(0.0, raw))
    return float(1.0 + clipped)  # maps [-1,0] → [0,1]


class ModelScorer:
    # In-memory cache: (tenant_id_str, model_version) -> (model, registry_entry)
    _cache: dict[tuple[str, str], tuple[Any, ModelRegistry]] = {}

    async def score(
        self,
        tenant_id: str,
        features: dict[str, float],
        db: AsyncSession | None = None,
    ) -> dict:
        # 1. Resolve active model
        registry_entry = await self._get_active_model(tenant_id, db)

        if registry_entry is None:
            return {
                "anomaly_score": 0.5,
                "is_anomaly": False,
                "top_drivers": [],
                "model_version": None,
            }

        cache_key = (tenant_id, registry_entry.model_version)

        # 2. Load model from cache or MinIO
        if cache_key not in self._cache:
            model = self._download_model(registry_entry.artifact_path)
            if model is None:
                return {
                    "anomaly_score": 0.5,
                    "is_anomaly": False,
                    "top_drivers": [],
                    "model_version": registry_entry.model_version,
                }
            self._cache[cache_key] = (model, registry_entry)
        else:
            model, registry_entry = self._cache[cache_key]

        # 3. Build feature vector in training order
        feat_names: list[str] = registry_entry.feature_names or FEATURE_NAMES
        feature_vector = np.array(
            [features.get(f, 0.0) for f in feat_names], dtype=float
        ).reshape(1, -1)

        # 4. Predict
        prediction = model.predict(feature_vector)[0]   # 1 = normal, -1 = anomaly
        raw_score = float(model.score_samples(feature_vector)[0])
        anomaly_score = _normalize_score(raw_score)
        is_anomaly = prediction == -1

        # 5. Top drivers
        metrics = registry_entry.metrics or {}
        means = metrics.get("feature_means", [0.0] * len(feat_names))
        stds = metrics.get("feature_stds", [1.0] * len(feat_names))

        drivers = []
        for i, fname in enumerate(feat_names):
            val = features.get(fname, 0.0)
            std = stds[i] if i < len(stds) and stds[i] != 0 else 1.0
            mean = means[i] if i < len(means) else 0.0
            deviation = (val - mean) / std
            drivers.append({"feature": fname, "value": val, "deviation": deviation})

        top_drivers = sorted(drivers, key=lambda d: abs(d["deviation"]), reverse=True)[:5]

        return {
            "anomaly_score": round(anomaly_score, 6),
            "is_anomaly": is_anomaly,
            "top_drivers": top_drivers,
            "model_version": registry_entry.model_version,
        }

    async def _get_active_model(
        self, tenant_id: str, db: AsyncSession | None
    ) -> ModelRegistry | None:
        if db is None:
            return None
        try:
            tenant_uuid = uuid.UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
            result = await db.execute(
                select(ModelRegistry)
                .where(
                    ModelRegistry.tenant_id == tenant_uuid,
                    ModelRegistry.is_active.is_(True),
                )
                .limit(1)
            )
            return result.scalar_one_or_none()
        except Exception as exc:
            logger.error("DB lookup failed for tenant %s: %s", tenant_id, exc)
            return None

    def _download_model(self, artifact_path: str) -> Any | None:
        try:
            s3 = _get_minio_client()
            obj = s3.get_object(Bucket=settings.MINIO_BUCKET, Key=artifact_path)
            return pickle.loads(obj["Body"].read())  # noqa: S301
        except Exception as exc:
            logger.error("Failed to download model from MinIO (%s): %s", artifact_path, exc)
            return None
