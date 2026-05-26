"""
BetAML API — FastAPI application
Routes:
  /auth          - login, refresh, logout, /me
  /ingest        - file, event, batch, jobs, SSE stream
  /rules         - CRUD + simulate + compound rules + macros
  /alerts        - list, detail, triage, close, link-to-case, label
  /cases         - CRUD, assign, events, evidence, report-package
  /audit-logs    - listagem (ADMIN/AUDITOR)
  /players       - listagem + perfil + LGPD erasure
  /player-lists  - watchlists CRUD + CSV upload
  /reports       - monthly summary + PDF download
  /mappings      - CRUD MappingConfig
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast

import logging

import structlog
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi import (
    FastAPI,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select, text

# Adiciona libs ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings
from database import AsyncSessionLocal, current_tenant_id, engine
from libs.telemetry import init_opentelemetry_stub
from rate_limit import limiter  # Shared rate limiter (slowapi + Redis)
from models import (
    Base,
    FeatureSnapshot,
    Notification,
    Tenant,
    User,
)

logger = structlog.get_logger()

import re as _re
_CPF_RE_LOG = _re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL_RE_LOG = _re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PII_KEYS = frozenset({"cpf", "cpf_encrypted", "email", "full_name", "name", "phone", "password", "token", "access_token", "refresh_token"})

def _redact_value(v: object) -> object:
    if isinstance(v, str):
        v = _CPF_RE_LOG.sub("[REDACTED_CPF]", v)
        v = _EMAIL_RE_LOG.sub("[REDACTED_EMAIL]", v)
        return v
    if isinstance(v, dict):
        return {k: ("[REDACTED]" if str(k).lower() in _PII_KEYS else _redact_value(val)) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_redact_value(i) for i in v)
    return v

def _pii_redact_processor(logger, method, event_dict):  # noqa: ARG001
    for key in list(event_dict.keys()):
        if str(key).lower() in _PII_KEYS:
            event_dict[key] = "[REDACTED]"
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _pii_redact_processor,
        structlog.dev.ConsoleRenderer()
        if settings.environment in {"development", "test"}
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan: runs startup before yield, shutdown after yield."""
    await _startup()
    yield
    await _shutdown()


OPENAPI_TAGS = [
    {"name": "auth", "description": "Autenticacao, sessao atual, refresh token e logout."},
    {"name": "ingest", "description": "Conectores, ingestao batch/arquivo, jobs, streaming, DLQ e quarentena."},
    {"name": "rules", "description": "CRUD de regras DSL, simulacao, macros, listas e compound rules."},
    {"name": "features", "description": "Feature store online/offline, historico, drift e qualidade."},
    {"name": "alerts", "description": "Fila de alertas, triagem, labeling e explicabilidade de ML."},
    {"name": "cases", "description": "Workflow de casos, timeline, uploads, colaboracao e report packages."},
    {"name": "reports", "description": "Relatorios regulatorios mensais, export JSON/CSV e sumarizacao."},
    {"name": "admin", "description": "Tenant settings, onboarding, API keys, usuarios e maintenance mode."},
    {"name": "audit", "description": "Audit trail filtravel de acoes criticas e eventos com PII."},
    {"name": "players", "description": "Perfis de jogadores, dados financeiros, rede e LGPD."},
    {"name": "ml", "description": "Model registry, A/B testing, promocao e metricas de modelo."},
    {"name": "notifications", "description": "Notificacoes in-app e marcacao de leitura."},
    {"name": "search", "description": "Busca global por CPF, nome, case number e alert id."},
    {"name": "infra", "description": "Health, readiness e endpoints de infraestrutura."},
]


_is_dev = settings.environment in {"development", "test"}

app = FastAPI(
    title="BetAML API",
    description="PLD/FT Platform para Operadores de Apostas",
    version="2.1.0",
    # Swagger/ReDoc apenas em ambientes não-produtivos (evita exposição de schema em produção)
    docs_url="/docs" if _is_dev else None,
    redoc_url="/redoc" if _is_dev else None,
    openapi_url="/openapi.json" if _is_dev else None,
    contact={"name": "BetAML Compliance", "email": "compliance@betaml.io"},
    license_info={"name": "Proprietary"},
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)

# Attach slowapi limiter to app state
app.state.limiter = limiter


async def _rate_limit_exception_handler(request: Request, exc: Exception):
    return _rate_limit_exceeded_handler(request, cast(RateLimitExceeded, exc))


app.add_exception_handler(RateLimitExceeded, _rate_limit_exception_handler)

# CORS: em desenvolvimento aceita localhost explicitamente; produção exige CORS_ALLOW_ORIGINS.
# NÃO usar wildcard "*" pois allow_credentials=True + "*" é rejeitado pelos browsers
# e exporia a API a qualquer origem.
_cors_origins: list[str] = (
    ["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"]
    if _is_dev
    else [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Tenant-ID"],
)
app.add_middleware(SlowAPIMiddleware)

# New observability middlewares (registered after SlowAPI; add_middleware wraps in reverse,
# so RequestIDMiddleware runs outermost → RequestID generated before Maintenance check)
from middleware import RequestIDMiddleware, MaintenanceModeMiddleware  # noqa: E402
app.add_middleware(MaintenanceModeMiddleware)
app.add_middleware(RequestIDMiddleware)

# ─── Middleware: propaga tenant_id para RLS do Postgres ───────────────────────
@app.middleware("http")
async def set_rls_tenant_middleware(request: Request, call_next):
    from jose import jwt as _jwt, JWTError
    request.state.user_role = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = _jwt.decode(
                auth_header[7:], settings.jwt_secret, algorithms=[settings.jwt_algorithm]
            )
            tid = payload.get("tenant_id")
            role = payload.get("role")
            if tid:
                current_tenant_id.set(tid)
            if role:
                request.state.user_role = role
        except JWTError:
            pass  # token inválido — get_current_user rejeitará na rota
    try:
        response = await call_next(request)
    finally:
        current_tenant_id.set(None)  # evita vazamento entre requests no mesmo worker
    return response


# ─── Prometheus metrics ───────────────────────────
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_group_untemplated=True,
    excluded_handlers=["/metrics", "/health", "/docs", "/redoc", "/openapi.json"],
).instrument(app).expose(app, include_in_schema=False, tags=["observability"])

# ─── Producer global ──────────────────────────────
_producer = None
_feature_maintenance_task = None


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


async def _warm_feature_store_cache() -> None:
    """Warm Redis online store with latest persisted snapshots."""
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        warmed = 0
        async with AsyncSessionLocal() as db:
            tenant_ids = list((await db.execute(select(Tenant.id))).scalars().all())
            for tenant_id in tenant_ids:
                # RLS: set tenant context before reading feature_snapshots.
                try:
                    await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": str(tenant_id)})
                except Exception:
                    pass

                result = await db.execute(
                    text(
                        """
                        SELECT DISTINCT ON (player_id)
                               tenant_id, player_id, feature_date, features, created_at
                        FROM feature_snapshots
                        WHERE tenant_id = :tid
                        ORDER BY player_id, feature_date DESC, created_at DESC
                        """
                    ),
                    {"tid": str(tenant_id)},
                )
                rows = result.mappings().all()

                for row in rows:
                    features = dict(row.get("features") or {})
                    if not features:
                        continue
                    features.setdefault("snapshot_date", str(row.get("feature_date")))
                    features.setdefault("entity_type", "PLAYER")
                    features.setdefault("feature_version", int(features.get("feature_version", 2) or 2))
                    features.setdefault(
                        "snapshot_version",
                        int(features.get("snapshot_version", features.get("feature_version", 2)) or 2),
                    )
                    features.setdefault(
                        "gold_object_path",
                        (
                            f"gold/tenant_id={row['tenant_id']}/feature_date={row.get('feature_date')}/"
                            f"entity_type=PLAYER/player_id={row['player_id']}.json"
                        ),
                    )
                    features.setdefault("warmed_from", "feature_snapshot")
                    key = f"betaml:{row['tenant_id']}:features:{row['player_id']}"
                    await redis.hset(key, mapping={k: str(v) for k, v in features.items()})
                    await redis.expire(key, 14400)
                    warmed += 1

        await redis.aclose()
        logger.info("feature_store_cache_warmed", players=warmed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature_store_cache_warm_failed", error=str(exc))


def _feature_null_ratio(rows: list[FeatureSnapshot], key: str) -> float:
    if not rows:
        return 0.0
    missing = 0
    for row in rows:
        value = (row.features or {}).get(key)
        if value in (None, "", "null"):
            missing += 1
    return missing / max(len(rows), 1)


def _feature_mean(rows: list[FeatureSnapshot], key: str) -> float | None:
    values: list[float] = []
    for row in rows:
        value = (row.features or {}).get(key)
        if isinstance(value, bool):
            values.append(float(value))
            continue
        try:
            if value not in (None, "", "null"):
                values.append(float(value))
        except Exception:
            continue
    if not values:
        return None
    return sum(values) / len(values)


async def _run_feature_drift_check_once() -> None:
    try:
        async with AsyncSessionLocal() as db:
            tenant_ids = list((await db.execute(select(Tenant.id))).scalars().all())
            for tenant_id in tenant_ids:
                dates = list(
                    (
                        await db.execute(
                            select(FeatureSnapshot.feature_date)
                            .where(FeatureSnapshot.tenant_id == tenant_id)
                            .distinct()
                            .order_by(desc(FeatureSnapshot.feature_date))
                            .limit(2)
                        )
                    ).scalars().all()
                )
                if len(dates) < 2:
                    continue

                current_date, previous_date = dates[0], dates[1]
                current_rows = list(
                    (
                        await db.execute(
                            select(FeatureSnapshot).where(
                                FeatureSnapshot.tenant_id == tenant_id,
                                FeatureSnapshot.feature_date == current_date,
                            )
                        )
                    ).scalars().all()
                )
                previous_rows = list(
                    (
                        await db.execute(
                            select(FeatureSnapshot).where(
                                FeatureSnapshot.tenant_id == tenant_id,
                                FeatureSnapshot.feature_date == previous_date,
                            )
                        )
                    ).scalars().all()
                )
                if not current_rows or not previous_rows:
                    continue

                feature_keys = sorted(set().union(*[(row.features or {}).keys() for row in current_rows + previous_rows]))
                findings: list[str] = []
                drift_score = 0.0
                for key in feature_keys:
                    null_ratio = _feature_null_ratio(current_rows, key)
                    prev_null_ratio = _feature_null_ratio(previous_rows, key)
                    if null_ratio >= 0.30 and null_ratio - prev_null_ratio >= 0.20:
                        findings.append(f"{key}: null_ratio {null_ratio:.0%} (antes {prev_null_ratio:.0%})")
                        drift_score = max(drift_score, min(1.0, null_ratio))
                        continue

                    mean_now = _feature_mean(current_rows, key)
                    mean_prev = _feature_mean(previous_rows, key)
                    if mean_now is None or mean_prev is None:
                        continue
                    delta = abs(mean_now - mean_prev) / max(abs(mean_prev), 1.0)
                    if delta >= 0.50:
                        findings.append(f"{key}: media {mean_now:.2f} vs {mean_prev:.2f} ({delta:.0%})")
                        drift_score = max(drift_score, min(1.0, delta))

                if not findings:
                    continue

                title = f"Drift de features detectado em {current_date.isoformat()}"
                existing = (
                    await db.execute(
                        select(Notification).where(
                            Notification.tenant_id == tenant_id,
                            Notification.type == "FEATURE_DRIFT",
                            Notification.title == title,
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    continue

                admin_ids = list(
                    (
                        await db.execute(
                            select(User.id).where(
                                User.tenant_id == tenant_id,
                                User.role == "ADMIN",
                                User.active.is_(True),
                            )
                        )
                    ).scalars().all()
                )
                for admin_id in admin_ids:
                    db.add(
                        Notification(
                            tenant_id=tenant_id,
                            user_id=admin_id,
                            type="FEATURE_DRIFT",
                            title=title,
                            body="; ".join(findings[:5]),
                            is_read=False,
                        )
                    )

                for row in current_rows:
                    row.drift_score = drift_score

            await db.commit()
        logger.info("feature_drift_check_completed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("feature_drift_check_failed", error=str(exc))


async def _feature_store_maintenance_loop() -> None:
    while True:
        await _run_feature_drift_check_once()
        await asyncio.sleep(24 * 3600)


async def _startup():
    init_opentelemetry_stub("api")
    # Guard: JWT secret inseguro em ambientes não-dev
    if (
        settings.environment not in ("development", "test")
        and settings.jwt_secret == "dev-secret-change-me"
    ):
        raise RuntimeError(
            "⚠️  CRÍTICO: JWT_SECRET não pode ser o valor padrão em staging/produção. "
            "Gere um segredo com: python -c 'import secrets; print(secrets.token_hex(32))' "
            "e defina a variável de ambiente JWT_SECRET."
        )

    # Guard: comprimento mínimo obrigatório do JWT_SECRET (OWASP — HMAC-SHA256 requer ≥ 256 bits)
    if len(settings.jwt_secret.encode()) < 32:
        raise RuntimeError(
            "⚠️  CRÍTICO: JWT_SECRET deve ter no mínimo 32 bytes (256 bits) para garantir segurança HMAC-SHA256. "
            "Gere com: python -c 'import secrets; print(secrets.token_hex(32))'"
        )

    # Guard: PII encryption key insegura em ambientes não-dev
    if (
        settings.environment not in ("development", "test")
        and settings.pii_encryption_key == "ZGV2LXNlY3JldC1lbmNyeXB0aW9uLWtleS0zMmJ5"
    ):
        raise RuntimeError(
            "⚠️  CRÍTICO: PII_ENCRYPTION_KEY não pode ser o valor padrão em staging/produção. "
            "Gere uma chave com: python -c 'import secrets; print(secrets.token_urlsafe(32))' "
            "e defina a variável de ambiente PII_ENCRYPTION_KEY. "
            "ATENÇÃO: mudar a chave INVALIDA todos os CPFs criptografados no banco!"
        )

    # Guard: webhook secret inseguro em ambientes não-dev
    if (
        settings.environment not in ("development", "test")
        and settings.epsilon_webhook_secret == "dev-secret-change-me"
    ):
        raise RuntimeError(
            "⚠️  CRÍTICO: EPSILON_WEBHOOK_SECRET não pode ser o valor padrão em staging/produção. "
            "Defina a variável de ambiente EPSILON_WEBHOOK_SECRET com um segredo aleatório de ≥ 32 bytes."
        )

    if settings.environment not in ("development", "test"):
        ml_internal_api_key = os.getenv("ML_INTERNAL_API_KEY", "").strip()
        if not ml_internal_api_key:
            raise RuntimeError(
                "⚠️  CRÍTICO: ML_INTERNAL_API_KEY é obrigatório em staging/produção para autenticação interna com ml_service."
            )

    auto_create_schema = os.getenv("API_AUTO_CREATE_SCHEMA", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if auto_create_schema:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.warning("schema_auto_create_enabled")
    else:
        logger.info("schema_auto_create_disabled")

    if settings.environment not in ("development", "test"):
        async with AsyncSessionLocal() as db:
            has_alembic_version = bool(
                await db.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
            )
            if not has_alembic_version:
                raise RuntimeError(
                    "⚠️  CRÍTICO: alembic_version ausente em staging/produção. "
                    "Use estratégia única de migração via Alembic antes de subir a API."
                )
    await get_producer()
    await _setup_minio_lifecycle()
    await _warm_feature_store_cache()
    # O rules_engine é a autoridade padrão para materialização de alertas/cases.
    # O alert_processor legado fica opt-in apenas para cenários de migração controlada.
    alert_processor_enabled = os.getenv("ALERT_PROCESSOR_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if alert_processor_enabled and settings.environment not in {"development", "test"}:
        raise RuntimeError(
            "⚠️  CRÍTICO: ALERT_PROCESSOR_ENABLED não é permitido em staging/produção. "
            "O rules_engine é o materializador oficial de alertas/casos."
        )
    if alert_processor_enabled:
        try:
            from alert_processor import start_alert_consumer
            asyncio.create_task(start_alert_consumer(), name="alert_processor")
            logger.info("alert_processor_scheduled")
        except Exception as exc:
            logger.warning("alert_processor_start_failed", error=str(exc))
    else:
        logger.info("alert_processor_disabled_by_default")
    global _feature_maintenance_task
    _feature_maintenance_task = asyncio.create_task(
        _feature_store_maintenance_loop(),
        name="feature_store_maintenance",
    )

    # ── Sanctions / PEP checker — carregamento inicial ───────────────────────
    try:
        from sanctions import get_sanctions_checker
        _sc = get_sanctions_checker()
        _sc.reload()
        logger.info("sanctions_checker_loaded", total_entries=_sc.total_entries)
    except Exception as exc:
        logger.warning("sanctions_checker_load_failed", error=str(exc))

    # ── Scheduled jobs (APScheduler) ─────────────────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from jobs import calculate_risk_score_decay, cleanup_expired_player_data

        scheduler = AsyncIOScheduler(timezone="UTC")

        # ClickHouse Backfill (Features) — todo dia às 03:30 UTC
        from jobs import clickhouse_backfill_features_daily
        scheduler.add_job(
            clickhouse_backfill_features_daily,
            trigger="cron",
            hour=3,
            minute=30,
            id="clickhouse_backfill_features_daily",
            replace_existing=True,
            misfire_grace_time=7200,
        )

        # Risk Score Decay — todo dia às 04:00 UTC
        scheduler.add_job(
            calculate_risk_score_decay,
            trigger="cron",
            hour=4,
            minute=0,
            id="risk_score_decay",
            replace_existing=True,
            misfire_grace_time=3600,  # até 1h de tolerância
        )

        # LGPD Data Expiration — todo dia às 05:00 UTC
        scheduler.add_job(
            cleanup_expired_player_data,
            trigger="cron",
            hour=5,
            minute=0,
            id="lgpd_data_expiration",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # SLA Violations — a cada hora
        from jobs import check_sla_violations
        scheduler.add_job(
            check_sla_violations,
            trigger="interval",
            hours=1,
            id="sla_violations_check",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Feature Population Stats — todo dia às 06:00 UTC
        from jobs import compute_feature_population_stats
        scheduler.add_job(
            compute_feature_population_stats,
            trigger="cron",
            hour=6,
            minute=0,
            id="feature_population_stats",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Feature Drift Detection — todo dia às 07:00 UTC (após population_stats)
        # _run_feature_drift_check_once é uma função pura definida neste módulo;
        # não requer objetos mock de request ou usuário.
        scheduler.add_job(
            _run_feature_drift_check_once,
            trigger="cron",
            hour=7,
            minute=0,
            id="feature_drift_detection",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Data Quality Alerting — todo dia às 06:30 UTC
        from jobs import data_quality_alerting
        scheduler.add_job(
            data_quality_alerting,
            trigger="cron",
            hour=6,
            minute=30,
            id="data_quality_alerting",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Data Retention Batch — toda semana domingo às 03:00 UTC
        from jobs import data_retention_batch
        scheduler.add_job(
            data_retention_batch,
            trigger="cron",
            day_of_week="sun",
            hour=3,
            minute=0,
            id="data_retention_batch",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Business Metrics Update — a cada 5 minutos
        from metrics import update_business_metrics
        scheduler.add_job(
            update_business_metrics,
            trigger="interval",
            minutes=5,
            id="business_metrics_update",
            replace_existing=True,
            misfire_grace_time=60,
        )

        # Sanctions / PEP list reload — todo dia às 06:00 UTC
        from sanctions import reload_sanctions
        scheduler.add_job(
            reload_sanctions,
            trigger="cron",
            hour=6,
            minute=0,
            id="sanctions_reload",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # COAF Reporting Deadline Monitor — todo dia às 08:00 UTC
        from jobs import check_coaf_reporting_deadlines
        scheduler.add_job(
            check_coaf_reporting_deadlines,
            trigger="cron",
            hour=8,
            minute=0,
            id="coaf_deadline_check",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Champion-Challenger Auto-Promotion — todo dia às 07:30 UTC
        from jobs import auto_promote_challenger_models
        scheduler.add_job(
            auto_promote_challenger_models,
            trigger="cron",
            hour=7,
            minute=30,
            id="challenger_auto_promotion",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        scheduler.start()
        logger.info(
            "scheduled_jobs_started",
            jobs=[
                "clickhouse_backfill_features_daily@03:30",
                "risk_score_decay@04:00",
                "lgpd_data_expiration@05:00",
                "sla_violations_check@1h",
                "feature_population_stats@06:00",
                "data_quality_alerting@06:30",
                "challenger_auto_promotion@07:30",
                "coaf_deadline_check@08:00",
                "data_retention_batch@sun03:00",
                "business_metrics_update@5min",
            ],
        )
    except ImportError:
        logger.warning("apscheduler_not_installed", hint="pip install apscheduler")
    except Exception as exc:
        logger.warning("scheduler_start_failed", error=str(exc))

    logger.info("betaml_api_started", env=settings.environment)


async def _shutdown():
    global _feature_maintenance_task
    if _feature_maintenance_task:
        _feature_maintenance_task.cancel()
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
    """Backward-compatible liveness endpoint used by README/dev smoke checks."""
    return {"status": "live"}


# ═══════════════════════════════════════════════════
# ROUTERS — cada domínio em seu próprio módulo
# ═══════════════════════════════════════════════════

from routers import auth, alerts, audit, cases, ingest, mappings, players, rules, external_validation  # noqa: E402
from routers.admin import router as admin_router                # noqa: E402
from routers.compound_rules import router as compound_rules_router  # noqa: E402
from routers.feature_store import router as feature_store_router  # noqa: E402
from routers.health import router as health_router                # noqa: E402
from routers.ml import router as ml_router                     # noqa: E402
from routers.notifications import router as notifications_router  # noqa: E402
from routers.internal import router as internal_router            # noqa: E402
from routers.player_lists import router as player_lists_router    # noqa: E402
from routers.reports import router as reports_router              # noqa: E402
from routers.search import router as search_router                # noqa: E402
from routers.stats import router as stats_router                  # noqa: E402
from routers.sanctions import router as sanctions_router          # noqa: E402

app.include_router(auth.router)
app.include_router(alerts.router)
app.include_router(audit.router)
app.include_router(cases.router)
app.include_router(compound_rules_router)
app.include_router(health_router)
app.include_router(ingest.router)
app.include_router(mappings.router)
app.include_router(player_lists_router)
app.include_router(players.router)
app.include_router(reports_router)
app.include_router(rules.router)
app.include_router(admin_router)
app.include_router(feature_store_router)
app.include_router(ml_router)
app.include_router(notifications_router)
app.include_router(internal_router)
app.include_router(search_router)
app.include_router(stats_router)
app.include_router(external_validation.router)
app.include_router(sanctions_router)
