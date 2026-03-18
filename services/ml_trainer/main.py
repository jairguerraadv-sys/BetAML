"""
ml_trainer — Scheduled ML model retraining service.

Roda diariamente via APScheduler às 03:00 UTC:
  1. Busca alerts com feedback labels (TRUE_POSITIVE / FALSE_POSITIVE) dos últimos 30 dias
  2. Extrai feature vectors do campo evidence JSONB
  3. Modo SUPERVISIONADO (>= 50 amostras labeladas):
       - Treina GradientBoostingClassifier usando X e y (TRUE_POSITIVE=1 / FALSE_POSITIVE=0)
       - Registra model_type="GradientBoosting" no model_registry
  4. Modo NÃO-SUPERVISIONADO (< 50 amostras labeladas — fallback):
       - Busca todos os alerts recentes para montar X sem labels
       - Treina IsolationForest com contamination proporcional
       - Registra model_type="IsolationForest" no model_registry
  5. Avalia métricas (precision, recall, F1)
  6. Persiste modelo no MinIO (betaml-models/<tipo>_v{version}.pkl)
  7. Registra no model_registry (champion auto-promovido se F1 > 0.75)
  8. Notifica ADMINs ativos via Notification
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import pickle
import sys
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
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


def _extract_feature_vector(alert) -> list[float] | None:
    """Extrai vetor de features do campo evidence JSONB de um alert.

    Retorna None se o alert não possui features válidas.
    """
    features = (alert.evidence or {}).get("features", {})
    if not features:
        return None
    return [float(features.get(col, 0) or 0) for col in FEATURE_COLUMNS]


async def retrain_isolation_forest() -> None:
    """
    Retreinar modelo ML com alerts dos últimos 30 dias.

    Modo SUPERVISIONADO (GradientBoosting): ativado quando há >= 50 alertas
    com labels TRUE_POSITIVE / FALSE_POSITIVE e feature vectors válidos.
    Constrói y (TRUE_POSITIVE=1 / FALSE_POSITIVE=0) e treina GradientBoostingClassifier.

    Modo NÃO-SUPERVISIONADO (IsolationForest): fallback quando < 50 amostras
    labeladas. Busca todos os alerts recentes (máx 5000) para compor X sem labels.
    Pula se não houver feature vectors suficientes (< 50).

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

            cutoff = datetime.now(UTC) - timedelta(days=30)
            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

            # ── 1. Buscar alerts LABELADOS dos últimos 30 dias ──────────────────
            stmt_labeled = select(Alert).where(
                Alert.label.in_(["TRUE_POSITIVE", "FALSE_POSITIVE"]),
                Alert.created_at >= cutoff,
            )
            labeled_alerts = (await db.execute(stmt_labeled)).scalars().all()

            # ── 2. Extrair features + labels dos alerts labelados ───────────────
            X_labeled: list[list[float]] = []
            y_labeled: list[int] = []
            for alert in labeled_alerts:
                vec = _extract_feature_vector(alert)
                if vec is None:
                    continue
                X_labeled.append(vec)
                y_labeled.append(1 if alert.label == "TRUE_POSITIVE" else 0)

            labeled_sample_count = len(X_labeled)

            # ── 3. Decide training mode ─────────────────────────────────────────
            if labeled_sample_count >= 50:
                # ────────────────────────────────────────────────────────────────
                # MODO SUPERVISIONADO: GradientBoostingClassifier
                # Usa X e y com labels TRUE_POSITIVE=1 / FALSE_POSITIVE=0
                # ────────────────────────────────────────────────────────────────
                logger.info(
                    "training_mode",
                    mode="supervised",
                    labeled_samples=labeled_sample_count,
                )

                X_arr = np.array(X_labeled, dtype=float)
                y_arr = np.array(y_labeled, dtype=int)

                model = GradientBoostingClassifier(
                    n_estimators=100,
                    max_depth=3,
                    learning_rate=0.1,
                    random_state=42,
                )
                model.fit(X_arr, y_arr)

                # Avaliação in-sample (proxy; produção deve usar validação cruzada)
                preds_bin = model.predict(X_arr)
                preds_prob = model.predict_proba(X_arr)[:, 1]
                precision = float(precision_score(y_arr, preds_bin, zero_division=0))
                recall = float(recall_score(y_arr, preds_bin, zero_division=0))
                f1 = float(f1_score(y_arr, preds_bin, zero_division=0))
                try:
                    auc_roc = float(roc_auc_score(y_arr, preds_prob))
                except ValueError:
                    auc_roc = 0.0

                model_type = "GradientBoosting"
                model_filename = f"gradient_boosting_v{version}.pkl"
                training_metadata = {
                    "n_estimators": 100,
                    "max_depth": 3,
                    "learning_rate": 0.1,
                    "training_samples": labeled_sample_count,
                    "true_positives": int(y_arr.sum()),
                    "false_positives": int((y_arr == 0).sum()),
                    "training_window_days": 30,
                    "feature_columns": FEATURE_COLUMNS,
                }

            else:
                # ────────────────────────────────────────────────────────────────
                # MODO NÃO-SUPERVISIONADO: IsolationForest (fallback)
                # Busca TODOS os alerts recentes (não só labelados) para compor X
                # ────────────────────────────────────────────────────────────────
                logger.info(
                    "training_mode",
                    mode="unsupervised",
                    labeled_samples=labeled_sample_count,
                )

                stmt_all = (
                    select(Alert)
                    .where(Alert.created_at >= cutoff)
                    .limit(5000)
                )
                all_alerts = (await db.execute(stmt_all)).scalars().all()

                X_all: list[list[float]] = []
                y_all: list[int] = []  # usado apenas para contamination estimate
                for alert in all_alerts:
                    vec = _extract_feature_vector(alert)
                    if vec is None:
                        continue
                    X_all.append(vec)
                    y_all.append(1 if alert.label == "TRUE_POSITIVE" else 0)

                if len(X_all) < 50:
                    logger.warning(
                        "ml_training_skipped_not_enough_feature_vectors",
                        count=len(X_all),
                        labeled=labeled_sample_count,
                        minimum=50,
                    )
                    return

                X_arr = np.array(X_all, dtype=float)
                y_arr = np.array(y_all, dtype=int)

                # Contamination proporcional aos positivos conhecidos
                contamination = float(
                    max(0.01, min(0.5, y_arr.sum() / max(len(y_arr), 1)))
                )

                model = IsolationForest(
                    n_estimators=100,
                    contamination=contamination,
                    random_state=42,
                    n_jobs=-1,
                )
                model.fit(X_arr)

                # Avaliação in-sample: -1 = anomaly, 1 = normal → binariza para 0/1
                preds = model.predict(X_arr)
                preds_bin = np.where(preds == -1, 1, 0)
                # decision_function: scores negativos = mais anômalo; invertemos para AUC
                anomaly_scores = -model.decision_function(X_arr)
                precision = float(precision_score(y_arr, preds_bin, zero_division=0))
                recall = float(recall_score(y_arr, preds_bin, zero_division=0))
                f1 = float(f1_score(y_arr, preds_bin, zero_division=0))
                try:
                    auc_roc = float(roc_auc_score(y_arr, anomaly_scores))
                except ValueError:
                    auc_roc = 0.0

                model_type = "IsolationForest"
                model_filename = f"isolation_forest_v{version}.pkl"
                training_metadata = {
                    "contamination": contamination,
                    "n_estimators": 100,
                    "training_samples": len(X_all),
                    "true_positives": int(y_arr.sum()),
                    "training_window_days": 30,
                    "feature_columns": FEATURE_COLUMNS,
                }

            # ── 4. Log métricas ─────────────────────────────────────────────────
            logger.info(
                "ml_training_metrics",
                model_type=model_type,
                precision=round(precision, 4),
                recall=round(recall, 4),
                f1=round(f1, 4),
                auc_roc=round(auc_roc, 4),
                samples=len(X_arr),
            )

            run_id = f"{model_type.lower()}-{version}"
            metrics_artifact = {
                "run_id": run_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "model_type": model_type,
                "version": version,
                "metrics": {
                    "precision": precision,
                    "recall": recall,
                    "f1_score": f1,
                    "auc_roc": auc_roc,
                },
                "training_metadata": training_metadata,
                "samples": len(X_arr),
                "window_days": 30,
            }

            # ── 5. Persiste modelo no MinIO ─────────────────────────────────────
            model_bytes = pickle.dumps(model)
            minio_client.put_object(
                bucket_name=bucket,
                object_name=model_filename,
                data=io.BytesIO(model_bytes),
                length=len(model_bytes),
                content_type="application/octet-stream",
            )

            metrics_filename = f"metrics/{model_type.lower()}_metrics_v{version}.json"
            metrics_bytes = json.dumps(metrics_artifact).encode("utf-8")
            minio_client.put_object(
                bucket_name=bucket,
                object_name=metrics_filename,
                data=io.BytesIO(metrics_bytes),
                length=len(metrics_bytes),
                content_type="application/json",
            )
            logger.info("ml_model_persisted", filename=model_filename, bytes=len(model_bytes))
            logger.info("ml_metrics_persisted", filename=metrics_filename, run_id=run_id)

            # ── 6. Registra no model_registry ───────────────────────────────────
            # Busca champion atual para comparação de regressão de qualidade
            current_champion = (
                await db.execute(
                    select(ModelRegistry)
                    .where(ModelRegistry.status == "champion")
                    .order_by(ModelRegistry.trained_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            champion_precision = (
                (current_champion.metrics or {}).get("precision", 0.0)
                if current_champion else 0.0
            )

            # Critérios de promoção: F1 > 0.75 E sem regressão de precision > 5%
            precision_regression = (
                champion_precision > 0.0
                and precision < champion_precision * 0.95
            )
            is_champion = f1 > 0.75 and not precision_regression

            if precision_regression:
                logger.warning(
                    "ml_champion_promotion_blocked",
                    reason="precision_regression",
                    new_precision=round(precision, 4),
                    champion_precision=round(champion_precision, 4),
                    drop_pct=round((1 - precision / champion_precision) * 100, 1),
                )

            # De-promove champion anterior antes de promover o novo
            if is_champion and current_champion:
                current_champion.status = "archived"
                db.add(current_champion)

            registry_entry = ModelRegistry(
                model_name=model_type,
                model_type="ANOMALY",
                model_version=version,
                algorithm=model_type,
                artifact_uri=f"s3://{bucket}/{model_filename}",
                training_rows=len(X_arr),
                feature_columns=FEATURE_COLUMNS,
                metrics={
                    "run_id": run_id,
                    "metrics_artifact_uri": f"s3://{bucket}/{metrics_filename}",
                    "precision": precision,
                    "recall": recall,
                    "f1_score": f1,
                    "auc_roc": auc_roc,
                    **training_metadata,
                },
                status="champion" if is_champion else "STAGING",
                is_challenger=False,
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)
            await db.flush()  # gera ID antes de criar notificações

            # ── 7. Notifica ADMINs ──────────────────────────────────────────────
            admins = (
                await db.execute(
                    select(User).where(
                        User.role.in_(["ADMIN", "SUPER_ADMIN"]),
                        User.active.is_(True),
                    )
                )
            ).scalars().all()

            status_str = (
                f"Promovido a champion (F1={f1:.3f})"
                if is_champion
                else (
                    f"Bloqueado: regressão de precision {champion_precision:.3f}→{precision:.3f} (>{5}%)"
                    if precision_regression
                    else f"Abaixo do threshold 0.75 (F1={f1:.3f})"
                )
            )
            body = (
                f"Modelo {model_type} treinado com {len(X_arr)} amostras. "
                f"Precision={precision:.3f}, Recall={recall:.3f}, F1={f1:.3f}. "
                f"{status_str}"
            )

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title=f"Modelo {model_type} retreinado automaticamente",
                        body=body,
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()

            logger.info(
                "ml_training_completed",
                model_type=model_type,
                version=version,
                f1=round(f1, 4),
                auc_roc=round(auc_roc, 4),
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
