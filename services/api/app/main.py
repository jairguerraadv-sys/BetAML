import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import alerts, audit, auth, cases, ingest, rules

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to Redis
    app.state.redis = aioredis.from_url(
        settings.REDIS_URL, encoding="utf-8", decode_responses=True
    )
    logger.info("Redis connection established")
    yield
    # Shutdown: close Redis
    await app.state.redis.aclose()
    logger.info("Redis connection closed")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="BetAML AML Compliance API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(rules.router)
app.include_router(alerts.router)
app.include_router(cases.router)
app.include_router(audit.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": settings.PROJECT_NAME}
