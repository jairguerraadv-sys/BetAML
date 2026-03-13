"""
ml_trainer — Scheduled ML model retraining service.

Roda diariamente via APScheduler às 03:00 UTC:
  1. Busca alerts com feedback labels (TRUE_POSITIVE / FALSE_POSITIVE) dos últimos 30 dias
  2. Extrai feature vectors do campo evidence JSONB
  3. Treina IsolationForest com contamination proporcional aos positivos
  4. Avalia métricas (precision, recall, F1)
  5. Persiste modelo no MinIO (betaml-models/isolation_forest_v{version}.pkl)
  6. Registra no model_registry (champion auto-promovido se F1 > 0.75)
  7. Notifica ADMINs ativos via Notification
"""
from __future__ import annotations

import asyncio
import io
import os
import pickle
import sys
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Resolve shared libs
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from config import settings  # noqa: E402

logger = structlog.get_logger(__name__)

_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(_url, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)

# Feature columns expected in alert.evidence["features"]
FEATURE_COLUMNS = [
    "deposit_sum_24h",
    "deposit_sum_7d",
    "deposit_count_7d",
    "withdrawal_sum_24h",
    "withdrawal_sum_7d",
    "cashout_ratio_30d",
    "velocity_score",
    "night_activity_ratio",
    "round_amount_ratio",
    "avg_bet_stake",
    "bet_count_7d",
    "win_loss_ratio_30d",
    "structuring_score",
    "layering_score",
    "rapid_cashout_score",
    "pep_flag",
    "account_age_days",
    "login_count_7d",
    "unique_payment_methods_30d",
    "deposit_withdrawal_gap_hours",
    "high_risk_events_count",
    "network_centrality_score",
    "ml_anomaly_score",
    "composite_risk_score",
]


async def retrain_isolation_forest() -> None:
    """
    Retreinar IsolationForest com alerts labelados dos últimos 30 dias.

    Pula se houver menos de 50 amostras com feature vectors válidos.
    Auto-promove a champion se F1 > 0.75.
    """
    try:
        from minio import Minio

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https://"),
        )

        # Garante que o bucket existe
        bucket = "betaml-models"
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)
            logger.info("minio_bucket_created", bucket=bucket)

        async with Session() as db:
            # Importação lazy para evitar conflito com sys.path em outros módulos
            from models import Alert, ModelRegistry, Notification, User  # noqa: PLC0415

            # 1. Buscar alerts com labels dos últimos 30 dias
            cutoff = datetime.now(UTC) - timedelta(days=30)
            stmt = select(Alert).where(
                Alert.label.in_(["TRUE_POSITIVE", "FALSE_POSITIVE"]),
                Alert.created_at >= cutoff,
            )
            alerts = (await db.execute(stmt)).scalars().all()

            if len(alerts) < 50:
                logger.warning(
                    "ml_training_skipped_insufficient_labels",
                    count=len(alerts),
                    minimum=50,
                )
                return

            # 2. Extrair features + labels
            X: list[list[float]] = []
            y: list[int] = []
            for alert in alerts:
                features = (alert.evidence or {}).get("features", {})
                if not features:
                    continue
                vector = [float(features.get(col, 0) or 0) for col in FEATURE_COLUMNS]
                X.append(vector)
                y.append(1 if alert.label == "TRUE_POSITIVE" else 0)

            if len(X) < 50:
                logger.warning(
                    "ml_training_skipped_not_enough_feature_vectors", count=len(X)
                )
                return

            X_arr = np.array(X, dtype=float)
            y_arr = np.array(y, dtype=int)

            # 3. Treinar IsolationForest
            contamination = float(max(0.01, min(0.5, y_arr.sum() / len(y_arr))))
            model = IsolationForest(
                n_estimators=100,
                contamination=contamination,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X_arr)

            # 4. Avaliar métricas (treino in-sample — proxy para bootstrapping)
            preds = model.predict(X_arr)           # -1 = anomaly, 1 = normal
            preds_bin = np.where(preds == -1, 1, 0)

            precision = float(precision_score(y_arr, preds_bin, zero_division=0))
            recall = float(recall_score(y_arr, preds_bin, zero_division=0))
            f1 = float(f1_score(y_arr, preds_bin, zero_division=0))

            logger.info(
                "ml_training_metrics",
                precision=round(precision, 4),
                recall=round(recall, 4),
                f1=round(f1, 4),
                samples=len(X),
                contamination=round(contamination, 4),
            )

            # 5. Persiste modelo no MinIO
            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            model_filename = f"isolation_forest_v{version}.pkl"
            model_bytes = pickle.dumps(model)

            minio_client.put_object(
                bucket_name=bucket,
                object_name=model_filename,
                data=io.BytesIO(model_bytes),
                length=len(model_bytes),
                content_type="application/octet-stream",
            )
            logger.info("ml_model_persisted", filename=model_filename, bytes=len(model_bytes))

            # 6. Registra no model_registry
            is_champion = f1 > 0.75
            registry_entry = ModelRegistry(
                model_type="IsolationForest",
                version=version,
                artifact_uri=f"s3://{bucket}/{model_filename}",
                metadata={
                    "contamination": contamination,
                    "n_estimators": 100,
                    "training_samples": len(X),
                    "true_positives": int(y_arr.sum()),
                    "training_window_days": 30,
                    "feature_columns": FEATURE_COLUMNS,
                },
                metrics={
                    "precision": precision,
                    "recall": recall,
                    "f1_score": f1,
                },
                is_champion=is_champion,
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)
            await db.flush()  # gera ID antes de criar notificações

            # 7. Notificar ADMINs
            admins = (
                await db.execute(
                    select(User).where(
                        User.role.in_(["ADMIN", "SUPER_ADMIN"]),
                        User.active.is_(True),
                    )
                )
            ).scalars().all()

            status_str = (
                f"✓ Promovido a champion (F1={f1:.3f})"
                if is_champion
                else f"⚠️ Abaixo do threshold 0.75 (F1={f1:.3f})"
            )
            body = (
                f"Modelo treinado com {len(X)} amostras. "
                f"Precision={precision:.3f}, Recall={recall:.3f}, F1={f1:.3f}. "
                f"{status_str}"
            )

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title="Modelo ML retreinado automaticamente",
                        body=body,
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()

            logger.info(
                "ml_training_completed",
                version=version,
                f1=round(f1, 4),
                is_champion=is_champion,
                admins_notified=len(admins),
            )

    except Exception:
        logger.exception("ml_training_failed")


async def main() -> None:
    """Inicializa o scheduler e mantém o processo vivo."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Treina todo dia às 03:00 UTC (antes do risk_score_decay às 04:00)
    scheduler.add_job(
        retrain_isolation_forest,
        trigger="cron",
        hour=3,
        minute=0,
        id="ml_training",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("ml_trainer_started", schedule="03:00 UTC daily")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
