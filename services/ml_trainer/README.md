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
The ml_trainer service runs **4 scheduled jobs**:

| Job | Schedule | Description |
|-----|----------|-------------|
| **Anomaly Detection** | Daily 03:00 UTC | GradientBoosting/IsolationForest (main model) |
| **Structuring Detector** | Daily 03:15 UTC | RandomForest especializado em depósitos fracionados |
| **Network Clustering** | Weekly Sun 04:00 UTC | DBSCAN para detectar redes de players interconectados |
| **Recurrence Estimator** | Weekly Sat 05:00 UTC | k-NN para detectar padrões recorrentes vs baseline de risco |

All jobs have `misfire_grace_time` of 1-2 hours if container was down.

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
A model is auto-promoted to **champion** when:
- **GradientBoosting**: in-sample F1 score > 0.75 AND no precision regression > 5% vs current champion
- **StructuringDetector**: F1 > 0.70
- **NetworkClustering**: always champion (unsupervised clustering, no classification)
- **RecurrenceEstimator**: always champion (scoring model, not binary classifier)

All active ADMIN and SUPER_ADMIN users receive an in-app `Notification` of type `ML_TRAINING_COMPLETED` after every training run.

---

## Specialized Models (Module 4)

### 1. Structuring Detector
**File**: `structuring_detector.py`
**Algorithm**: RandomForestClassifier (150 estimators, max_depth=6, class_weight='balanced')
**Purpose**: Detect structuring (fracionamento) — multiple deposits to evade detection threshold
**Features** (10):
- `deposit_count_24h`, `deposit_count_7d`, `deposit_velocity`
- `unique_instruments_used_7d`, `avg_time_between_deposit_and_withdrawal_7d`
- `deposit_sum_24h`, `deposit_sum_7d`
- `night_activity_ratio`, `round_amount_ratio`, `structuring_score`

**Training**: Daily at 03:15 UTC. Requires >= 30 labeled alerts (last 60 days) with keywords "STRUCTURING", "FRACIONAMENTO", or "MÚLTIPLOS DEPÓSITOS" in title.
**Promotion threshold**: F1 > 0.70

---

### 2. Network Clustering
**File**: `network_clustering.py`
**Algorithm**: DBSCAN (eps=0.3, min_samples=3) + StandardScaler
**Purpose**: Detect suspicious player clusters (shared devices, shared bank accounts, mule networks)
**Features** (3):
- `shared_device_score`, `shared_instrument_score`, `cluster_size`

**Training**: Weekly (Sunday 04:00 UTC). Processes all ACTIVE/PEP/HIGH_RISK players from last 90 days.
**Output**: Updates `Player.cluster_id` (MD5 hash) and `Player.cluster_size` in DB. Clusters with size >= 5 are flagged as suspicious.
**No promotion logic** — always champion (unsupervised model).

---

### 3. Recurrence Estimator
**File**: `recurrence_estimator.py`
**Algorithm**: k-NN (k=5, euclidean distance) + StandardScaler
**Purpose**: Detect recurrence patterns — players with behavior similar to previously ERASED/REPORTED/CLOSED accounts (reincidência)
**Features** (8):
- `device_fingerprint_hash_int`, `ip_hash_int`
- `hour_of_day_mode`, `day_of_week_mode`
- `avg_transaction_amount`, `transaction_frequency_per_hour`
- `avg_bet_stake`, `deposit_to_withdrawal_ratio`

**Training**: Weekly (Saturday 05:00 UTC). Fits k-NN on baseline of ERASED/REPORTED players (min 5 samples), then scores all ACTIVE players.
**Output**: Updates `Player.features["recurrence_score"]` (0-1) and flags players with score > 0.85 as `recurrence_suspect=True`.
**No promotion logic** — always champion (scoring model, not classifier).

## Kafka Topics
Not applicable — this service writes directly to Postgres and MinIO and does not consume or produce Kafka messages.
