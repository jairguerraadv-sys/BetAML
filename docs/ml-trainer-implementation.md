# ML Training Scheduler — Implementação Automática

## Objetivo

Retreinar o modelo de anomalia (IsolationForest) automaticamente a cada dia usando feedback labels (TRUE_POSITIVE/FALSE_POSITIVE) dos analistas.

## Arquitetura Target

```
┌──────────────────────────────────────────────────────────────┐
│ ml_trainer service (novo)                                    │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ APScheduler (cron 03:00 UTC)                             │ │
│ │   ↓                                                       │ │
│ │ 1. Busca alerts com label != NULL (últimos 30d)          │ │
│ │ 2. Busca features de cada alert                          │ │
│ │ 3. Treina IsolationForest (contamination auto-calculated) │ │
│ │ 4. Avalia métricas (precision, recall, F1)               │ │
│ │ 5. Persiste modelo no MinIO                              │ │
│ │ 6. Registra no model_registry (champion se F1 > 0.75)    │ │
│ │ 7. Notifica ADMIN via Notification                       │ │
│ └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
         ↓
    PostgreSQL (alerts, model_registry)
         ↓
    MinIO (betaml-models/isolation_forest_v{version}.pkl)
```

## Passos de Implementação

### 1. Criar `services/ml_trainer/main.py`

```python
"""
ml_trainer — Scheduled ML model retraining service.

Roda diariamente via APScheduler:
  - Busca alerts com feedback labels dos últimos 30 dias
  - Treina IsolationForest
  - Avalia métricas
  - Persiste modelo no MinIO
  - Registra no model_registry
"""
from __future__ import annotations

import asyncio
import os
import pickle
import sys
from datetime import UTC, datetime, timedelta

import numpy as np
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from config import settings
from models import Alert, ModelRegistry, Tenant

logger = structlog.get_logger(__name__)

_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
engine = create_async_engine(_url, echo=False)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def retrain_isolation_forest() -> None:
    """
    Retrain IsolationForest using labeled alerts from the last 30 days.

    Steps:
      1. Fetch alerts with label in (TRUE_POSITIVE, FALSE_POSITIVE) from last 30d
      2. Extract feature vectors from alert.evidence JSONB
      3. Train IsolationForest with contamination=auto
      4. Evaluate metrics (precision, recall, F1)
      5. Persist model to MinIO (betaml-models/isolation_forest_v{version}.pkl)
      6. Register in model_registry with metrics
      7. If F1 > 0.75, promote to champion
    """
    try:
        from minio import Minio

        minio_client = Minio(
            settings.minio_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )

        async with Session() as db:
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
            X = []
            y = []
            for alert in alerts:
                features = alert.evidence.get("features", {})
                if not features:
                    continue
                # Extrair vector de features (mesmo formato do ml_service)
                feature_vector = [
                    features.get("deposit_sum_24h", 0),
                    features.get("deposit_count_7d", 0),
                    features.get("withdrawal_sum_24h", 0),
                    features.get("cashout_ratio_30d", 0),
                    features.get("velocity_score", 0),
                    # ... adicionar todos os 24 features usados no scoring
                ]
                X.append(feature_vector)
                y.append(1 if alert.label == "TRUE_POSITIVE" else 0)

            if len(X) < 50:
                logger.warning("ml_training_skipped_not_enough_feature_vectors", count=len(X))
                return

            X = np.array(X)
            y = np.array(y)

            # 3. Treinar IsolationForest
            contamination = max(0.01, min(0.5, y.sum() / len(y)))  # proporção de positivos
            model = IsolationForest(
                n_estimators=100,
                contamination=contamination,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(X)

            # 4. Avaliar métricas
            predictions = model.predict(X)  # -1 = anomaly, 1 = normal
            predictions_binary = np.where(predictions == -1, 1, 0)

            precision = precision_score(y, predictions_binary, zero_division=0)
            recall = recall_score(y, predictions_binary, zero_division=0)
            f1 = f1_score(y, predictions_binary, zero_division=0)

            logger.info(
                "ml_training_metrics",
                precision=precision,
                recall=recall,
                f1=f1,
                samples=len(X),
                contamination=contamination,
            )

            # 5. Persiste modelo no MinIO
            version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            model_filename = f"isolation_forest_v{version}.pkl"
            model_bytes = pickle.dumps(model)

            import io
            minio_client.put_object(
                bucket_name="betaml-models",
                object_name=model_filename,
                data=io.BytesIO(model_bytes),
                length=len(model_bytes),
                content_type="application/octet-stream",
            )

            # 6. Registra no model_registry
            registry_entry = ModelRegistry(
                model_type="IsolationForest",
                version=version,
                artifact_uri=f"s3://betaml-models/{model_filename}",
                metadata={
                    "contamination": contamination,
                    "n_estimators": 100,
                    "training_samples": len(X),
                    "true_positives": int(y.sum()),
                    "training_window_days": 30,
                },
                metrics={
                    "precision": float(precision),
                    "recall": float(recall),
                    "f1_score": float(f1),
                },
                is_champion=(f1 > 0.75),  # Auto-promote se F1 > 0.75
                trained_at=datetime.now(UTC),
            )
            db.add(registry_entry)

            # 7. Criar notificação para ADMINs
            from models import Notification, User

            admins = (
                await db.execute(
                    select(User).where(User.role.in_(["ADMIN", "SUPER_ADMIN"]), User.active == True)
                )
            ).scalars().all()

            for admin in admins:
                db.add(
                    Notification(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        type="ML_TRAINING_COMPLETED",
                        title="Modelo ML retreinado automaticamente",
                        body=f"Novo modelo treinado com {len(X)} amostras. F1={f1:.3f}, Precision={precision:.3f}, Recall={recall:.3f}. {'✓ Promovido a champion' if f1 > 0.75 else '⚠️ Métricas abaixo do threshold 0.75'}",
                        reference_type="ModelRegistry",
                        reference_id=str(registry_entry.id),
                    )
                )

            await db.commit()

            logger.info(
                "ml_training_completed",
                version=version,
                f1=f1,
                is_champion=(f1 > 0.75),
            )

    except Exception as exc:
        logger.error("ml_training_failed", error=str(exc), exc_info=True)


async def main():
    """Start scheduler and keep alive."""
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

    # Keep alive
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Criar `services/ml_trainer/requirements.txt`

```txt
sqlalchemy[asyncio]==2.0.29
asyncpg==0.29.0
apscheduler==3.10.4
structlog==24.1.0
scikit-learn==1.4.2
numpy==1.26.4
minio==7.2.7
```

### 3. Criar `services/ml_trainer/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY ../../libs /app/libs
COPY ../../services/api/config.py /app/config.py
COPY ../../services/api/models.py /app/models.py
COPY ../../services/api/database.py /app/database.py

CMD ["python", "main.py"]
```

### 4. Adicionar ao `docker-compose.yml`

```yaml
  ml-trainer:
    build:
      context: ../
      dockerfile: services/ml_trainer/Dockerfile
    container_name: betaml-ml-trainer
    env_file: ../.env
    depends_on:
      - postgres
      - minio
    networks:
      - betaml-net
    restart: unless-stopped
```

### 5. Criar bucketMinIO `betaml-models` (adicionar ao init-minio.sh)

```bash
mc mb minio/betaml-models
mc policy set download minio/betaml-models  # read-only público para inference
```

---

## Testes

### Teste manual
```bash
# 1. Popular alerts com labels
curl -X PATCH http://localhost:8000/alerts/{alert_id}/label \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"label": "TRUE_POSITIVE", "comment": "Confirmed structuring"}'

# 2. Trigger manual do training
docker exec -it betaml-ml-trainer python -c "import asyncio; from main import retrain_isolation_forest; asyncio.run(retrain_isolation_forest())"

# 3. Verificar model_registry
curl http://localhost:8000/ml/models | jq '.[] | {version, f1_score: .metrics.f1_score, is_champion}'
```

---

## Status Atual

✅ **Arquivo de implementação criado**: `docs/ml-trainer-implementation.md`
⏳ **Próximos passos**:
1. Criar pasta `services/ml_trainer/`
2. Implementar `main.py` conforme spec acima
3. Adicionar ao docker-compose.yml
4. Testar com 50+ alerts labelados
5. Monitorar métricas no Grafana (adicionar dashboard ML Training)

## Estimativa

- Implementação completa: **3 dias**
- Testes + validação: **1 dia**
- **Total: 4 dias**

---

## TODOs para Dev

- [ ] Criar `services/ml_trainer/main.py`
- [ ] Criar `services/ml_trainer/requirements.txt`
- [ ] Criar `services/ml_trainer/Dockerfile`
- [ ] Adicionar serviço `ml-trainer` ao docker-compose.yml
- [ ] Criar bucket `betaml-models` no MinIO
- [ ] Adicionar variável `ML_TRAINING_ENABLED=true` no .env
- [ ] Testar com dataset de 50+ alerts labelados
- [ ] Criar dashboard Grafana "ML Training Metrics"
- [ ] Adicionar alertas Prometheus se F1 < 0.70
- [ ] Documentar no analyst-guide.md como interpretar métricas
