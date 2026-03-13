# ML Trainer

## Purpose
Standalone scheduled service that retrains the ML model daily at **03:00 UTC**. Selects between supervised and unsupervised training based on the availability of analyst feedback labels, then persists the artefact to MinIO and registers the result in the `model_registry` Postgres table.

## Prerequisites
Docker + docker-compose OR Python 3.11+

## Environment Variables
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://betaml:devpass@localhost:5432/betaml_dev` | PostgreSQL connection string (asyncpg driver required) |
| `MINIO_ENDPOINT` | `http://localhost:9000` | MinIO / S3-compatible endpoint for model artefact storage |
| `MINIO_ACCESS_KEY` | `minio` | MinIO access key |
| `MINIO_SECRET_KEY` | `minio123` | MinIO secret key |

All settings are loaded through `services/api/config.py` (Settings via pydantic-settings), so they can also be provided as environment variables or via a `.env` file.

## Running Locally
```bash
cd services/ml_trainer
pip install -r requirements.txt
python main.py
```

## Schedule
The training job runs once per day at **03:00 UTC** (before the `risk_score_decay` job at 04:00). The APScheduler `misfire_grace_time` is 3600 seconds — if the container was down at the scheduled time, the job will fire within the next hour.

## Training Modes
### Supervised — GradientBoostingClassifier
Activated when there are **>= 50 alerts** with `label IN ('TRUE_POSITIVE', 'FALSE_POSITIVE')` and valid feature vectors within the last 30-day window.

- Builds `X` from `alert.evidence["features"]` (24 columns) and `y` (TRUE_POSITIVE=1, FALSE_POSITIVE=0)
- Trains `sklearn.ensemble.GradientBoostingClassifier` (100 estimators, depth 3, lr 0.1)
- Records `model_type = "GradientBoosting"` in `model_registry`
- Artefact filename: `gradient_boosting_v{YYYYMMDDHHMMSS}.pkl`

### Unsupervised — IsolationForest (fallback)
Used when fewer than 50 labeled samples are available.

- Queries all alerts from the last 30 days (up to 5 000) regardless of label
- `contamination` is set proportionally to the fraction of known positives
- Records `model_type = "IsolationForest"` in `model_registry`
- Artefact filename: `isolation_forest_v{YYYYMMDDHHMMSS}.pkl`
- Skips training if fewer than 50 feature vectors can be extracted

## Champion Promotion
A model is auto-promoted to **champion** when its in-sample F1 score exceeds **0.75**. All active ADMIN and SUPER_ADMIN users receive an in-app `Notification` of type `ML_TRAINING_COMPLETED` after every training run.

## Kafka Topics
Not applicable — this service writes directly to Postgres and MinIO and does not consume or produce Kafka messages.
