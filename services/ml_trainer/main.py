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
            supervised_mode = labeled_sample_count >= 50

            # ── 3. Decide training mode ─────────────────────────────────────────
            if supervised_mode:
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

            # Critérios de promoção: apenas treino supervisionado pode auto-promover.
            # Treino não supervisionado continua em STAGING para revisão controlada.
            precision_regression = (
                champion_precision > 0.0
                and precision < champion_precision * 0.95
            )
            is_champion = supervised_mode and f1 > 0.75 and not precision_regression

            if not supervised_mode:
                logger.info(
                    "ml_champion_promotion_skipped",
                    reason="unsupervised_training_requires_manual_review",
                    model_type=model_type,
                )

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


async def _train_structuring_detector_job() -> None:
    """Wrapper job para StructuringDetector com tratamento de exceções."""
    try:
        from minio import Minio

        from structuring_detector import train_structuring_detector  # noqa: PLC0415

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https://"),
        )

        bucket = "betaml-models"
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        async with Session() as db:
            result = await train_structuring_detector(db, minio_client, bucket)

            if result is None:
                logger.info("structuring_detector_skipped_insufficient_data")
                return

            # Registra no model_registry
            from models import ModelRegistry, Notification, User  # noqa: PLC0415

            registry_entry = ModelRegistry(
                tenant_id=None,  # modelo global multi-tenant
                model_name=result["model_name"],
                model_type=result["model_type"],
                algorithm=result["algorithm"],
                model_version=result["model_version"],
                artifact_uri=result["artifact_uri"],
                training_rows=result["training_rows"],
                feature_columns=result["feature_columns"],
                metrics=result["metrics"],
                status="STAGING",
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)

            # Auto-promove se F1 > 0.70
            f1 = result["metrics"]["f1_score"]
            if f1 > 0.70:
                registry_entry.status = "champion"
                logger.info("structuring_detector_promoted", f1=f1)

            # Notifica ADMINs
            admins = (
                await db.execute(
                    select(User).where(
                        User.role.in_(["ADMIN", "SUPER_ADMIN"]),
                        User.active.is_(True),
                    )
                )
            ).scalars().all()

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title="Structuring Detector retreinado",
                        body=f"Precision={result['metrics']['precision']:.3f}, F1={f1:.3f}",
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()
            logger.info("structuring_detector_job_success", f1=f1)

    except Exception:
        logger.exception("structuring_detector_job_failed")


async def _train_network_clustering_job() -> None:
    """Wrapper job para Network Clustering com tratamento de exceções."""
    try:
        from minio import Minio

        from network_clustering import train_network_clustering  # noqa: PLC0415

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https://"),
        )

        bucket = "betaml-models"
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        async with Session() as db:
            result = await train_network_clustering(db, minio_client, bucket, tenant_id=None)

            if result is None:
                logger.info("network_clustering_skipped_insufficient_data")
                return

            # Registra no model_registry
            from models import ModelRegistry, Notification, User  # noqa: PLC0415

            registry_entry = ModelRegistry(
                tenant_id=None,
                model_name=result["model_name"],
                model_type=result["model_type"],
                algorithm=result["algorithm"],
                model_version=result["model_version"],
                artifact_uri=result["artifact_uri"],
                training_rows=result["training_rows"],
                feature_columns=result["feature_columns"],
                metrics=result["metrics"],
                status="champion",  # sempre champion (não é supervisionado)
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)

            # Notifica ADMINs
            admins = (
                await db.execute(
                    select(User).where(
                        User.role.in_(["ADMIN", "SUPER_ADMIN"]),
                        User.active.is_(True),
                    )
                )
            ).scalars().all()

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title="Network Clustering concluído",
                        body=f"Clusters detectados: {result['metrics']['n_clusters']}, Suspeitos: {result['metrics']['suspicious_clusters_count']}",
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()
            logger.info("network_clustering_job_success", clusters=result["metrics"]["n_clusters"])

    except Exception:
        logger.exception("network_clustering_job_failed")


async def _train_recurrence_estimator_job() -> None:
    """Wrapper job para Recurrence Estimator com tratamento de exceções."""
    try:
        from minio import Minio

        from recurrence_estimator import train_recurrence_estimator  # noqa: PLC0415

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https://"),
        )

        bucket = "betaml-models"
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        async with Session() as db:
            result = await train_recurrence_estimator(db, minio_client, bucket, tenant_id=None)

            if result is None:
                logger.info("recurrence_estimator_skipped_insufficient_baseline")
                return

            # Registra no model_registry
            from models import ModelRegistry, Notification, User  # noqa: PLC0415

            registry_entry = ModelRegistry(
                tenant_id=None,
                model_name=result["model_name"],
                model_type=result["model_type"],
                algorithm=result["algorithm"],
                model_version=result["model_version"],
                artifact_uri=result["artifact_uri"],
                training_rows=result["training_rows"],
                feature_columns=result["feature_columns"],
                metrics=result["metrics"],
                status="champion",  # sempre champion (é scoring, não classificação)
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)

            # Notifica ADMINs
            admins = (
                await db.execute(
                    select(User).where(
                        User.role.in_(["ADMIN", "SUPER_ADMIN"]),
                        User.active.is_(True),
                    )
                )
            ).scalars().all()

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title="Recurrence Estimator retreinado",
                        body=f"Players suspeitos identificados: {result['metrics'].get('suspicious_count', 0)}",
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()
            logger.info(
                "recurrence_estimator_job_success",
                suspicious=result["metrics"].get("suspicious_count", 0),
            )

    except Exception:
        logger.exception("recurrence_estimator_job_failed")


async def bootstrap_model_if_needed() -> None:
    """Garante que existe ao menos um modelo IsolationForest champion no registry.

    Chamado no startup do ml_trainer. Se o model_registry estiver vazio
    (ambiente novo, container recém-criado ou banco recém-migrado), treina um
    IsolationForest mínimo com dados sintéticos realistas para que o ml_service
    nunca inicie sem modelo disponível.

    O modelo gerado recebe status="bootstrap" em vez de "champion" para que
    analistas saibam que é um modelo inicial sem dados reais. Será substituído
    automaticamente pelo primeiro ciclo de treino diário quando houver
    histórico suficiente (>= 50 alertas com feature vectors).
    """
    try:
        from minio import Minio  # noqa: PLC0415

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_endpoint.startswith("https://"),
        )
        bucket = "betaml-models"
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)

        async with Session() as db:
            from models import ModelRegistry  # noqa: PLC0415

            # Verifica se já existe qualquer modelo no registry
            existing = (await db.execute(
                select(ModelRegistry).limit(1)
            )).scalar_one_or_none()

            if existing is not None:
                logger.info(
                    "ml_bootstrap_skip_model_exists",
                    model_id=str(existing.id),
                    status=existing.status,
                )
                return

            logger.warning(
                "ml_bootstrap_no_model_found",
                hint="Gerando IsolationForest sintético de bootstrap. "
                     "Será substituído no próximo ciclo de treino diário.",
            )

            # ── Gera 100 amostras sintéticas com distribuição realista ──────────
            rng = np.random.default_rng(seed=42)
            n_samples = 100

            # Cada feature tem média e desvio realistas baseado no domínio PLD
            _feature_stats = [
                # (mean, std) por feature em FEATURE_COLUMNS
                (5000, 3000),   # deposit_sum_24h
                (20000, 15000), # deposit_sum_7d
                (8, 5),         # deposit_count_7d
                (3000, 2000),   # withdrawal_sum_24h
                (12000, 8000),  # withdrawal_sum_7d
                (0.4, 0.3),     # cashout_ratio_30d
                (0.5, 0.3),     # velocity_score
                (0.2, 0.15),    # night_activity_ratio
                (0.1, 0.1),     # round_amount_ratio
                (200, 150),     # avg_bet_stake
                (15, 10),       # bet_count_7d
                (1.0, 0.5),     # win_loss_ratio_30d
                (0.3, 0.25),    # structuring_score
                (0.2, 0.2),     # layering_score
                (0.25, 0.2),    # rapid_cashout_score
                (0.05, 0.22),   # pep_flag (Bernoulli ~5%)
                (365, 200),     # account_age_days
                (12, 8),        # login_count_7d
                (2, 1.5),       # unique_payment_methods_30d
                (6, 5),         # deposit_withdrawal_gap_hours
                (2, 2),         # high_risk_events_count
                (0.3, 0.2),     # network_centrality_score
                (0.2, 0.15),    # ml_anomaly_score
                (0.3, 0.2),     # composite_risk_score
            ]
            X_bootstrap = np.zeros((n_samples, len(FEATURE_COLUMNS)), dtype=float)
            for i, (mean, std) in enumerate(_feature_stats):
                X_bootstrap[:, i] = np.clip(rng.normal(mean, std, n_samples), 0, None)

            # Injeta ~10% de anomalias sintéticas
            n_anomalies = n_samples // 10
            anomaly_idx = rng.choice(n_samples, n_anomalies, replace=False)
            X_bootstrap[anomaly_idx] *= rng.uniform(3.0, 5.0, (n_anomalies, len(FEATURE_COLUMNS)))

            model = IsolationForest(
                n_estimators=50,
                contamination=0.10,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X_bootstrap)

            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "_bootstrap"
            model_filename = f"isolation_forest_v{version}.pkl"
            model_bytes = pickle.dumps(model)

            minio_client.put_object(
                bucket_name=bucket,
                object_name=model_filename,
                data=io.BytesIO(model_bytes),
                length=len(model_bytes),
                content_type="application/octet-stream",
            )

            artifact_uri = f"s3://{bucket}/{model_filename}"
            registry_entry = ModelRegistry(
                model_name="IsolationForest",
                model_type="ANOMALY",
                model_version=version,
                algorithm="IsolationForest",
                artifact_uri=artifact_uri,
                training_rows=n_samples,
                feature_columns=FEATURE_COLUMNS,
                metrics={
                    "note": "Bootstrap sintético — substitua com treino real",
                    "contamination": 0.10,
                    "n_estimators": 50,
                    "training_samples": n_samples,
                    "synthetic": True,
                },
                # "bootstrap" indica que não é um modelo produtivo;
                # o ml_service deve reconhecer este status como "champion fallback".
                status="bootstrap",
                is_active=True,
                is_challenger=False,
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)
            await db.commit()

            logger.info(
                "ml_bootstrap_model_created",
                artifact_uri=artifact_uri,
                version=version,
                hint="Modelo sintético ativo. Será substituído no próximo ciclo diário (03:00 UTC).",
            )

    except Exception:
        logger.exception("ml_bootstrap_failed")


async def main() -> None:
    """Inicializa o scheduler e mantém o processo vivo."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Bootstrap: garante que sempre há um modelo disponível antes dos jobs periódicos
    await bootstrap_model_if_needed()

    # Job 1: Treino principal (GradientBoosting/IsolationForest) - Diário 03:00 UTC
    scheduler.add_job(
        retrain_isolation_forest,
        trigger="cron",
        hour=3,
        minute=0,
        id="ml_training",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Job 2: Structuring Detector - Diário 03:15 UTC (após treino principal)
    scheduler.add_job(
        _train_structuring_detector_job,
        trigger="cron",
        hour=3,
        minute=15,
        id="structuring_detector",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Job 3: Network Clustering - Semanal Domingo 04:00 UTC
    scheduler.add_job(
        _train_network_clustering_job,
        trigger="cron",
        day_of_week="sun",
        hour=4,
        minute=0,
        id="network_clustering",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    # Job 4: Recurrence Estimator - Semanal Sábado 05:00 UTC
    scheduler.add_job(
        _train_recurrence_estimator_job,
        trigger="cron",
        day_of_week="sat",
        hour=5,
        minute=0,
        id="recurrence_estimator",
        replace_existing=True,
        misfire_grace_time=7200,
    )

    scheduler.start()
    logger.info(
        "ml_trainer_started",
        jobs=[
            "ml_training (daily 03:00)",
            "structuring_detector (daily 03:15)",
            "network_clustering (weekly Sun 04:00)",
            "recurrence_estimator (weekly Sat 05:00)",
        ],
    )

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
