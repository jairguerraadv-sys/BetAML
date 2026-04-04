# ML Service

## Purpose
FastAPI microservice that provides real-time ML scoring and on-demand model training. Supports multiple model types (IsolationForest, GradientBoosting StructuringDetector, DBSCAN GraphClustering, k-NN RecurrenceEstimator) with A/B champion / challenger testing and SHAP-based explainability. Model artefacts are stored in MinIO and registered in the `model_registry` Postgres table.

## Prerequisites
Docker + docker-compose OR Python 3.11+

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://betaml:devpass@localhost:5432/betaml_dev` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL (feature store, not directly used at inference time) |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO / S3-compatible endpoint for model artefacts |
| `MINIO_ACCESS_KEY` | `minio` | MinIO access key |
| `MINIO_SECRET_KEY` | `minio123` | MinIO secret key |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse host used for training data loading |
| `ENVIRONMENT` | `development` | Controla comportamento fail-closed de scoring e treino sintético |
| `ML_ALLOW_SYNTHETIC_TRAINING` | unset | Reabilita treino sintético explicitamente fora de `development`/`test` |

## Running Locally
```bash
cd services/ml_service
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001
```

## Key Endpoints
| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health check |
| `POST` | `/score` | Online scoring — returns `anomaly_score` [0,1] and top drivers |
| `POST` | `/score/shap` | SHAP-style per-feature importance for a score |
| `POST` | `/score/ab` | Score against both champion and challenger models |
| `POST` | `/train` | Trigger IsolationForest training for a tenant |
| `POST` | `/train/structuring` | Train supervised GradientBoosting StructuringDetector |
| `POST` | `/train/graph` | Train DBSCAN graph clustering model |
| `POST` | `/train/recurrence` | Train k-NN recurrence / pattern estimator |
| `GET` | `/models` | List model registry entries for a tenant (`X-Tenant-Id` header required) |
| `POST` | `/models/reload` | Invalidate in-memory model cache for a tenant |
| `GET` | `/models/{model_id}/ab-metrics` | A/B test metrics for a specific model version |

## Runtime Hardening
In `staging` and `production`, online scoring now fails closed with HTTP 503 when no champion model is available for the tenant. Synthetic training paths are blocked by default outside `development` and `test`; override only with `ML_ALLOW_SYNTHETIC_TRAINING=true` for controlled bootstrap workflows.

## Kafka Topics
Not applicable — this service exposes a REST API. It does not consume or produce Kafka messages directly.

## Model Artefact Storage
Artefacts are stored in MinIO bucket `betaml-models` at path `{tenant_id}/{model_id}.pkl`. The service uses an in-process LRU cache keyed by `{tenant_id}:{model_type}` to avoid repeated MinIO downloads.

## Scheduled Retraining
An embedded APScheduler job triggers IsolationForest retraining for all active tenants daily at **03:00 UTC**. The dedicated `ml-trainer` service (see `services/ml_trainer/`) provides the supervised retraining path using feedback labels.
