# ML Service: FastAPI for model training and scoring
# Provides endpoints for anomaly detection using IsolationForest

import os
import json
import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
import pickle
from uuid import uuid4

from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel
import numpy as np
from sklearn.ensemble import IsolationForest
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://betaml:devpass@postgres:5432/betaml_dev")
REDIS_URL = os.getenv("REDIS_URL", "redis://:devpass@redis:6379/0")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minio123")
ML_MODEL_BUCKET = os.getenv("ML_MODEL_BUCKET", "betaml-models")

# Initialize
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
redis_client = redis.from_url(REDIS_URL, decode_responses=False)

app = FastAPI(
    title="BetAML ML Service",
    description="Machine Learning anomaly detection",
    version="1.0.0"
)

# ===================== SCHEMAS =====================

class ScoringRequest(BaseModel):
    tenant_id: str
    player_id: str
    features: Dict[str, Any]

class ScoringResponse(BaseModel):
    anomaly_score: float
    is_anomaly: bool
    top_drivers: List[Dict[str, float]]
    model_version: int

class TrainingRequest(BaseModel):
    tenant_id: str
    dataset_window_days: int = 90

class TrainingResponse(BaseModel):
    model_version: int
    trained_at: str
    metrics: Dict[str, Any]

# ===================== DEPENDENCIES =====================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ===================== MODEL MANAGEMENT =====================

class ModelManager:
    """Manages model training, loading, and versioning."""
    
    def __init__(self, redis_client, db_session):
        self.redis = redis_client
        self.db = db_session
    
    def get_active_model(self, tenant_id: str):
        """Load active model for tenant from Redis cache."""
        cache_key = f"model:active:{tenant_id}".encode()
        cached = self.redis.get(cache_key)
        
        if cached:
            model_data = json.loads(cached.decode())
            return pickle.loads(model_data['model_bytes']), model_data['version']
        
        # TODO: Load from S3/MinIO or DB
        return None, None
    
    def train_model(self, tenant_id: str, features_list: List[Dict[str, float]]) -> tuple:
        """Train IsolationForest model on features."""
        
        # Convert features to numpy array
        feature_names = list(features_list[0].keys()) if features_list else []
        X = np.array([[f.get(name, 0) for name in feature_names] for f in features_list])
        
        # Handle NaN/inf
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Train IsolationForest
        model = IsolationForest(
            contamination=0.1,
            random_state=42,
            n_estimators=100
        )
        model.fit(X)
        
        # Get model version
        result = self.db.execute(
            text("SELECT COALESCE(MAX(model_version), 0) + 1 as next_version FROM model_registry WHERE tenant_id = :tenant_id AND model_type = 'ISOLATION_FOREST'"),
            {"tenant_id": tenant_id}
        ).first()
        next_version = result[0] if result else 1
        
        # Calculate metrics
        scores = model.score_samples(X)
        anomalies = model.predict(X)  # -1 for anomalies, 1 for normal
        
        metrics = {
            "n_samples": len(X),
            "n_features": X.shape[1],
            "n_anomalies": int((anomalies == -1).sum()),
            "mean_score": float(np.mean(scores)),
            "std_score": float(np.std(scores))
        }
        
        # Cache model in Redis
        model_bytes = pickle.dumps(model)
        cache_data = {
            "model_bytes": model_bytes.hex(),
            "version": next_version,
            "trained_at": datetime.utcnow().isoformat()
        }
        cache_key = f"model:active:{tenant_id}".encode()
        self.redis.setex(cache_key, 86400*7, json.dumps(cache_data).encode())  # 7-day cache
        
        # Record in DB
        self.db.execute(
            text("""
                INSERT INTO model_registry (tenant_id, model_type, model_version, artifact_path, trained_at, dataset_window_days, metrics, is_active)
                VALUES (:tenant_id, 'ISOLATION_FOREST', :version, :path, :trained_at, :window, :metrics, true)
            """),
            {
                "tenant_id": tenant_id,
                "version": next_version,
                "path": f"s3://{ML_MODEL_BUCKET}/{tenant_id}/isolation-forest-v{next_version}.pkl",
                "trained_at": datetime.utcnow(),
                "window": 90,
                "metrics": json.dumps(metrics)
            }
        )
        self.db.commit()
        
        logger.info(f"Trained model v{next_version} for tenant {tenant_id}: {metrics}")
        
        return model, next_version, metrics
    
    def score(self, model, features: Dict[str, float]) -> Dict[str, Any]:
        """Score features using model."""
        
        feature_names = sorted(features.keys())
        X = np.array([[features.get(name, 0) for name in feature_names]]).reshape(1, -1)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        
        # Get anomaly score (-1 to 1, higher = more anomalous)
        anomaly_pred = model.predict(X)[0]
        raw_score = model.score_samples(X)[0]
        
        # Normalize score to 0-1
        # Isolation Forest returns negative scores for anomalies
        anomaly_score = 1 / (1 + np.exp(raw_score))  # Sigmoid normalization
        
        # Identify top drivers (features with highest deviations)
        feature_importance = np.abs(X[0])
        top_indices = np.argsort(feature_importance)[-5:][::-1]
        
        top_drivers = [
            {"feature": feature_names[i], "value": float(X[0][i])}
            for i in top_indices
        ]
        
        return {
            "anomaly_score": float(anomaly_score),
            "is_anomaly": anomaly_pred == -1,
            "top_drivers": top_drivers,
            "raw_score": float(raw_score)
        }

# ===================== ENDPOINTS =====================

@app.post("/score", response_model=ScoringResponse, tags=["scoring"])
async def score(request: ScoringRequest, db: Session = Depends(get_db)):
    """Score features for anomaly detection."""
    
    manager = ModelManager(redis_client, db)
    
    # Load model
    model, version = manager.get_active_model(request.tenant_id)
    
    if not model:
        raise HTTPException(status_code=404, detail="No trained model available")
    
    # Score
    result = manager.score(model, request.features)
    
    return ScoringResponse(
        anomaly_score=result['anomaly_score'],
        is_anomaly=result['is_anomaly'],
        top_drivers=result['top_drivers'],
        model_version=version or 1
    )

@app.post("/train", response_model=TrainingResponse, tags=["training"])
async def train_model(request: TrainingRequest, db: Session = Depends(get_db)):
    """Train anomaly detection model for tenant."""
    
    # TODO: Query features from ClickHouse/lakehouse for past N days
    # For now, generate synthetic features
    features_list = [
        {
            "deposit_sum_24h": np.random.exponential(100),
            "deposit_count_24h": np.random.poisson(5),
            "withdrawal_sum_24h": np.random.exponential(50),
            "bet_stake_sum_24h": np.random.exponential(150),
            "zscore_current_deposit_vs_baseline": np.random.normal(0, 1)
        }
        for _ in range(1000)
    ]
    
    manager = ModelManager(redis_client, db)
    model, version, metrics = manager.train_model(request.tenant_id, features_list)
    
    return TrainingResponse(
        model_version=version,
        trained_at=datetime.utcnow().isoformat(),
        metrics=metrics
    )

@app.get("/health", tags=["health"])
async def health():
    """Health check."""
    return {"status": "ok", "service": "ml-service"}

# ===================== ENTRY POINT =====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
