"""FastAPI application entry point."""

import logging

from fastapi import FastAPI

from ml.db import engine
from ml.models import Base
from ml.routers.scoring import router as scoring_router
from ml.routers.training import router as training_router
from ml.schemas import HealthResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="BetAML ML Service", version="1.0.0")


@app.on_event("startup")
async def on_startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialised")


app.include_router(training_router, tags=["training"])
app.include_router(scoring_router, tags=["scoring"])


@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
