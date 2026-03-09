"""
BetAML API — FastAPI application
Routes:
  /auth          - login, refresh, logout, /me
  /ingest        - file, event, batch, jobs
  /rules         - CRUD + simulate
  /alerts        - list, detail, triage, close, link-to-case
  /cases         - CRUD, assign, events, evidence, report-package
  /audit-logs    - listagem (ADMIN/AUDITOR)
  /players       - listagem + perfil
  /mappings      - CRUD MappingConfig
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

# Adiciona libs ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth import (
    create_access_token,
    decrypt_pii,
    encrypt_pii,
    get_current_user,
    hash_password,
    mask_cpf,
    oauth2_scheme,
    require_roles,
    revoke_token,
    verify_password,
)
from config import settings
from database import AsyncSessionLocal, current_tenant_id, engine, get_db
from models import (
    Alert,
    AuditLog,
    Base,
    Bet,
    Case,
    CaseEvent,
    FinancialTransaction,
    IngestJob,
    MappingConfig,
    Player,
    ReportPackage,
    RuleDefinition,
    ScoringConfig,
    Tenant,
    User,
)

logger = structlog.get_logger()

app = FastAPI(
    title="BetAML API",
    description="PLD/FT Platform para Operadores de Apostas",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Em desenvolvimento aceita localhost; em produção exige CORS_ALLOW_ORIGINS explícito.
_cors_origins: list[str] = (
    ["*"]
    if settings.environment == "development"
    else [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Middleware: propaga tenant_id para RLS do Postgres ───────────────────────
@app.middleware("http")
async def set_rls_tenant_middleware(request: Request, call_next):
    from jose import jwt as _jwt, JWTError
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = _jwt.decode(
                auth_header[7:], settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
            tid = payload.get("tenant_id")
            if tid:
                current_tenant_id.set(tid)
        except JWTError:
            pass  # token inválido — get_current_user rejeitará na rota
    try:
        response = await call_next(request)
    finally:
        current_tenant_id.set(None)  # evita vazamento entre requests no mesmo worker
    return response


# ─── Enterprise routes ────────────────────────────
from routes_enterprise import enterprise_router  # noqa: E402

# ─── Prometheus metrics ───────────────────────────
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_group_untemplated=True,
    excluded_handlers=["/metrics", "/health", "/docs", "/redoc", "/openapi.json"],
).instrument(app).expose(app, include_in_schema=False, tags=["observability"])

# ─── Producer global ──────────────────────────────
_producer = None


async def get_producer():
    global _producer
    if _producer is None:
        try:
            from libs.clients import KafkaProducerClient
            _producer = KafkaProducerClient(settings.kafka_bootstrap_servers)
            await _producer.start()
        except Exception as e:
            logger.warning("kafka_producer_unavailable", error=str(e))
    return _producer


# ─── Startup / shutdown ───────────────────────────

async def _setup_minio_lifecycle() -> None:
    """
    Garante que o bucket betaml-lakehouse existe e aplica lifecycle policies:
      - bronze/  → TTL 365 dias (dados brutos)
      - silver/  → TTL 180 dias (dados processados/canônicos)
      - gold/    → sem TTL (features diárias — retidas indefinidamente)
      - models/  → sem TTL (artefatos de modelos ML)
    """
    try:
        from minio import Minio
        from minio.lifecycleconfig import LifecycleConfig, Rule, Filter, Expiration  # type: ignore
        endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
        secure = settings.minio_endpoint.startswith("https://")
        client = Minio(endpoint, access_key=settings.minio_access_key,
                       secret_key=settings.minio_secret_key, secure=secure)

        for bucket in (settings.minio_bucket, "betaml-models"):
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
                logger.info("minio_bucket_created", bucket=bucket)

        lifecycle = LifecycleConfig(
            [
                Rule(
                    "Enabled",
                    rule_id="bronze-expire-365d",
                    rule_filter=Filter(prefix="bronze/"),
                    expiration=Expiration(days=365),
                ),
                Rule(
                    "Enabled",
                    rule_id="silver-expire-180d",
                    rule_filter=Filter(prefix="silver/"),
                    expiration=Expiration(days=180),
                ),
            ]
        )
        client.set_bucket_lifecycle(settings.minio_bucket, lifecycle)
        logger.info("minio_lifecycle_applied", bucket=settings.minio_bucket,
                    rules=["bronze:365d", "silver:180d"])
    except Exception as exc:
        # Lifecycle é best-effort no startup; não bloqueia a API
        logger.warning("minio_lifecycle_setup_failed", error=str(exc))


@app.on_event("startup")
async def startup():
    # Guard: JWT secret inseguro em ambientes não-dev
    if (
        settings.environment not in ("development", "test")
        and settings.jwt_secret == "dev-secret-change-me"
    ):
        raise RuntimeError(
            "JWT_SECRET não pode ser o valor padrão em ambientes de staging/produção. "
            "Gere um segredo com: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
    if (
        settings.environment not in ("development", "test")
        and settings.pii_encryption_key == "ZGV2LXNlY3JldC1lbmNyeXB0aW9uLWtleS0zMmJ5"
    ):
        raise RuntimeError(
            "PII_ENCRYPTION_KEY não pode ser o valor padrão em staging/produção."
        )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await get_producer()
    await _setup_minio_lifecycle()
    # Inicia background task para auto-criação de cases a partir de scoring.alerts
    try:
        from alert_processor import start_alert_consumer
        asyncio.create_task(start_alert_consumer(), name="alert_processor")
        logger.info("alert_processor_scheduled")
    except Exception as exc:
        logger.warning("alert_processor_start_failed", error=str(exc))
    logger.info("betaml_api_started", env=settings.environment)


@app.on_event("shutdown")
async def shutdown():
    if _producer:
        await _producer.stop()


# ─── Exception handlers ──────────────────────────

from sqlalchemy.exc import IntegrityError  # noqa: E402

@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    Converte violações de constraint do PostgreSQL (duplicate key, FK, NOT NULL)
    em HTTP 409 Conflict em vez de propagar como 500 Internal Server Error.
    """
    detail = str(exc.orig) if hasattr(exc, "orig") and exc.orig else str(exc)
    # Extrair mensagem legível (ex.: "duplicate key value violates unique constraint")
    if "duplicate key" in detail.lower():
        msg = "Registro duplicado: o valor informado já existe."
    elif "foreign key" in detail.lower():
        msg = "Referência inválida: entidade relacionada não encontrada."
    elif "not null" in detail.lower():
        msg = "Campo obrigatório ausente."
    else:
        msg = "Conflito de integridade de dados."
    logger.warning("integrity_error", detail=detail, path=str(request.url))
    return JSONResponse(status_code=409, content={"detail": msg})


# ─── Health ───────────────────────────────────────
@app.get("/health", tags=["infra"])
async def health():
    return {"status": "ok", "version": "2.1.0", "timestamp": datetime.utcnow().isoformat()}


# ═══════════════════════════════════════════════════
# ROUTERS — cada domínio em seu próprio módulo
# ═══════════════════════════════════════════════════

from routers import auth, alerts, audit, cases, ingest, mappings, players, rules  # noqa: E402

app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(audit.router)
app.include_router(cases.router)
app.include_router(ingest.router)
app.include_router(mappings.router)
app.include_router(players.router)
app.include_router(rules.router)

# Register enterprise routes after core routers so Module 1 endpoints
# (`/ingest/*` and `/mappings/*`) resolve to the newer handlers.
app.include_router(enterprise_router)

