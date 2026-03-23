"""
alert_processor.py — Consumer de scoring.alerts para auto-criação de Cases.

Responsabilidades:
  1. Consumir tópico scoring.alerts (Redpanda/Kafka)
  2. Para alertas com severity >= HIGH ou composite_score >= auto_case_threshold:
       - Verificar se já existe Case OPEN para o player
       - Se não: criar Case + CaseEvent inicial
       - Atualizar player.risk_score e player.risk_band
  3. Rodar como background task no startup da API (lifespan)

Integração:
  - Registrado em main.py via asyncio.create_task(start_alert_consumer())
  - Usa o mesmo DB engine e Redis que a API principal
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Alert, Case, CaseEvent, Player

logger = structlog.get_logger(__name__)

KAFKA_SERVERS   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC           = "scoring.alerts"
GROUP_ID        = "betaml-api-alert-processor"
MAX_RETRIES     = 5
RETRY_DELAY_S   = 5

# Severities que disparam auto-case
AUTO_CASE_SEVERITIES = {"CRITICAL", "HIGH"}

# ─── Métricas Prometheus ─────────────────────────────────────────────────────
_ALERTS_CREATED = Counter(
    "betaml_alerts_auto_created_total",
    "Total de alertas criados pelo alert_processor via scoring.alerts",
    ["severity", "tenant_id"],
)
_CASES_CREATED = Counter(
    "betaml_cases_auto_created_total",
    "Total de cases criados automaticamente pelo alert_processor",
    ["severity", "tenant_id"],
)
_PROC_DURATION = Histogram(
    "betaml_alert_processing_duration_seconds",
    "Duração do processamento de um evento scoring.alert pelo alert_processor",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
_CONSUMER_ERRORS = Counter(
    "betaml_alert_consumer_errors_total",
    "Total de erros ao consumir/processar mensagens do tópico scoring.alerts",
)

# Engine assíncrono para o consumer (criado on-demand)
_consumer_engine: Optional[object] = None
_consumer_session: Optional[async_sessionmaker] = None


def _get_consumer_session() -> async_sessionmaker:
    global _consumer_engine, _consumer_session
    if _consumer_session is None:
        url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        _consumer_engine = create_async_engine(url, echo=False, pool_size=2, max_overflow=0)
        _consumer_session = async_sessionmaker(_consumer_engine, expire_on_commit=False)
    return _consumer_session


def _compute_risk_band(score: float, low_threshold: float = 0.35, high_threshold: float = 0.70) -> str:
    if score >= high_threshold:
        return "HIGH"
    if score >= low_threshold:
        return "MEDIUM"
    return "LOW"


async def _get_scoring_config(db: AsyncSession, tenant_id: str) -> dict:
    """Carrega scoring_configs do tenant (com fallback para defaults)."""
    result = await db.execute(
        text("SELECT auto_case_threshold, risk_band_low_threshold, risk_band_high_threshold, "
             "income_volume_ratio_threshold, sla_high_hours, sla_critical_hours "
             "FROM scoring_configs WHERE tenant_id = :tid LIMIT 1"),
        {"tid": tenant_id}
    )
    row = result.fetchone()
    if row:
        return {
            "auto_case_threshold":       float(row.auto_case_threshold),
            "risk_band_low_threshold":   float(row.risk_band_low_threshold),
            "risk_band_high_threshold":  float(row.risk_band_high_threshold),
            "income_volume_ratio_threshold": float(row.income_volume_ratio_threshold),
            "sla_high_hours":            int(row.sla_high_hours),
            "sla_critical_hours":        int(row.sla_critical_hours),
        }
    return {
        "auto_case_threshold": 0.75,
        "risk_band_low_threshold": 0.35,
        "risk_band_high_threshold": 0.70,
        "income_volume_ratio_threshold": 1.5,
        "sla_high_hours": 24,
        "sla_critical_hours": 4,
    }


async def _process_alert_event(payload: dict, Session: async_sessionmaker) -> None:
    """
    Processa um evento de scoring.alert:
      - Persiste o Alert se ainda não existir (idempotência por source_event_id)
      - Atualiza player.risk_score / risk_band
      - Cria Case automaticamente se necessário
    """
    _timer = _PROC_DURATION.time()  # Histogram timer (context manager)
    tenant_id   = payload.get("tenant_id")
    player_id   = payload.get("player_id")
    severity    = payload.get("severity", "LOW")
    composite   = float(payload.get("composite_score", payload.get("score", 0.0)))
    source_eid  = payload.get("source_event_id") or str(uuid.uuid4())
    alert_id    = payload.get("alert_id")  # pode vir já persistido pelo rules_engine

    if not tenant_id:
        logger.warning("alert_processor_missing_tenant", payload=payload)
        return

    async with Session() as db:
        # RLS: set tenant context for this consumer session.
        # Without this, FORCE RLS would hide tenant rows (player/scoring_config/etc.).
        try:
            await db.execute(text("SELECT set_config('app.current_tenant', :tid, false)"), {"tid": tenant_id})
        except Exception:
            pass

        # ── Configurações do tenant ──────────────────────────────────────────
        cfg = await _get_scoring_config(db, tenant_id)

        # ── Verificar se o Alert já existe no banco ───────────────────────────
        alert = None
        if alert_id:
            alert = await db.get(Alert, alert_id)
        if alert is None and source_eid:
            res = await db.execute(
                select(Alert).where(
                    Alert.tenant_id     == tenant_id,
                    Alert.source_event_id == source_eid
                ).limit(1)
            )
            alert = res.scalar_one_or_none()

        # Se não existe, criar
        if alert is None:
            alert = Alert(
                tenant_id         = tenant_id,
                player_id         = player_id,
                alert_type        = payload.get("alert_type", "COMPOSITE"),
                severity          = severity,
                status            = "OPEN",
                title             = payload.get("title", f"Alerta automático — {severity}"),
                description       = payload.get("description"),
                evidence          = payload.get("evidence", {}),
                anomaly_score     = payload.get("anomaly_score"),
                composite_score   = composite if composite > 0 else None,
                score_breakdown   = payload.get("score_breakdown", {}),
                source_event_id   = source_eid,
            )
            db.add(alert)
            await db.flush()
            alert_id = alert.id
            logger.info("alert_processor_alert_created", alert_id=alert_id, tenant=tenant_id, severity=severity)
            _ALERTS_CREATED.labels(severity=severity, tenant_id=tenant_id).inc()

        # ── Atualizar player.risk_score e risk_band ──────────────────────────
        if player_id:
            player = await db.get(Player, player_id)
            if player and player.tenant_id == tenant_id:
                new_score = max(float(player.risk_score), composite) if composite > 0 else float(player.risk_score)
                new_band  = _compute_risk_band(
                    new_score,
                    cfg["risk_band_low_threshold"],
                    cfg["risk_band_high_threshold"]
                )
                player.risk_score     = round(new_score, 4)
                player.risk_band      = new_band
                player.last_scored_at = datetime.now(timezone.utc)
                logger.info("alert_processor_player_updated",
                            player_id=player_id, risk_score=new_score, risk_band=new_band)

                # ── Verificar compatibilidade renda/volume ───────────────────
                if player.declared_income_monthly:
                    deposit_30d = float(payload.get("features", {}).get("deposit_sum_30d", 0) or 0)
                    stake_30d   = float(payload.get("features", {}).get("stake_sum_30d", 0) or 0)
                    volume_30d  = deposit_30d + stake_30d
                    income_ratio_threshold = cfg["income_volume_ratio_threshold"]
                    income_monthly = float(player.declared_income_monthly)
                    if income_monthly > 0 and volume_30d > income_monthly * income_ratio_threshold:
                        # Criar alerta de incompatibilidade se ainda não existe
                        existing_income_alert = await db.execute(
                            select(Alert).where(
                                Alert.tenant_id == tenant_id,
                                Alert.player_id == player_id,
                                Alert.alert_type == "INCOME_INCOMPATIBILITY",
                                Alert.status.in_(["OPEN", "IN_REVIEW"]),
                            ).limit(1)
                        )
                        if not existing_income_alert.scalar_one_or_none():
                            ratio = round(volume_30d / income_monthly, 2)
                            income_alert = Alert(
                                tenant_id     = tenant_id,
                                player_id     = player_id,
                                alert_type    = "INCOME_INCOMPATIBILITY",
                                severity      = "HIGH" if ratio >= 2.0 else "MEDIUM",
                                status        = "OPEN",
                                title         = f"Incompatibilidade renda/volume — rácio {ratio}x",
                                description   = (
                                    f"Volume 30d (R${volume_30d:,.0f}) excede "
                                    f"{income_ratio_threshold}x a renda declarada "
                                    f"(R${income_monthly:,.0f}/mês). Rácio: {ratio}x"
                                ),
                                evidence      = {
                                    "volume_30d":         volume_30d,
                                    "deposit_sum_30d":    deposit_30d,
                                    "stake_sum_30d":      stake_30d,
                                    "income_monthly":     income_monthly,
                                    "ratio":              ratio,
                                    "threshold":          income_ratio_threshold,
                                },
                                source_event_id = f"income-compat-{player_id}-{datetime.now(timezone.utc).strftime('%Y%m')}",
                            )
                            db.add(income_alert)
                            logger.info("alert_processor_income_incompatibility",
                                        player_id=player_id, ratio=ratio)

        # ── Decisão de auto-case ─────────────────────────────────────────────
        should_create_case = (
            severity in AUTO_CASE_SEVERITIES
            or composite >= cfg["auto_case_threshold"]
        )

        if should_create_case and player_id and alert.id:
            # Verificar se já existe Case OPEN para o player
            existing_case = await db.execute(
                select(Case).where(
                    Case.tenant_id == tenant_id,
                    Case.player_id == player_id,
                    Case.status.in_(["OPEN", "INVESTIGATING"]),
                ).limit(1)
            )
            existing = existing_case.scalar_one_or_none()

            if existing:
                # Vincular alerta ao case existente e adicionar evento
                if not alert.case_id:
                    alert.case_id = existing.id
                evt = CaseEvent(
                    case_id    = existing.id,
                    tenant_id  = tenant_id,
                    event_type = "NOTE",
                    content    = {
                        "kind":           "AUTO_ALERT_LINKED",
                        "alert_id":       str(alert.id),
                        "severity":       severity,
                        "composite_score": composite,
                        "note":           f"Alerta automático vinculado ao caso existente (score={composite:.3f})"
                    },
                )
                db.add(evt)
                logger.info("alert_processor_alert_linked_to_existing",
                            case_id=existing.id, alert_id=alert.id)
            else:
                # Criar novo Case
                sla_hours = (
                    cfg["sla_critical_hours"] if severity == "CRITICAL"
                    else cfg["sla_high_hours"]
                )
                sla_due = datetime.now(timezone.utc) + timedelta(hours=sla_hours)
                case = Case(
                    tenant_id           = tenant_id,
                    player_id           = player_id,
                    title               = f"[AUTO] {alert.title}",
                    description         = (
                        f"Caso criado automaticamente pelo sistema de detecção PLD/FT.\n"
                        f"Severity: {severity} | Score: {composite:.3f} | "
                        f"Threshold: {cfg['auto_case_threshold']}"
                    ),
                    severity            = severity,
                    status              = "OPEN",
                    priority            = "HIGH" if severity == "CRITICAL" else "MEDIUM",
                    sla_due_at          = sla_due,
                    auto_created        = True,
                    auto_created_reason = (
                        f"scoring.alerts: severity={severity}, "
                        f"composite_score={composite:.3f}, "
                        f"threshold={cfg['auto_case_threshold']}"
                    ),
                    source_alert_id     = alert.id,
                )
                db.add(case)
                await db.flush()

                alert.case_id = case.id
                evt = CaseEvent(
                    case_id    = case.id,
                    tenant_id  = tenant_id,
                    event_type = "AUTO_CREATED",
                    content    = {
                        "alert_id":        str(alert.id),
                        "severity":        severity,
                        "composite_score": composite,
                        "rule_breakdown":  payload.get("score_breakdown", {}),
                        "note":            "Caso criado automaticamente pelo sistema de detecção PLD/FT",
                    },
                )
                db.add(evt)
                logger.info("alert_processor_case_created",
                            case_id=case.id, alert_id=alert.id, severity=severity, score=composite)
                _CASES_CREATED.labels(severity=severity, tenant_id=tenant_id).inc()

        await db.commit()
        _timer.stop()  # finaliza o histogram timer (prometheus_client.Timer)


async def start_alert_consumer() -> None:
    """
    Loop principal do consumer. Registrado como background task no startup da API.
    Usa a implementação de KafkaConsumerClient de libs/clients.py.
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

    Session = _get_consumer_session()

    logger.info("alert_consumer_starting", topic=TOPIC, group=GROUP_ID)
    _last_error_time = 0.0

    for attempt in range(MAX_RETRIES):
        try:
            from libs.clients import KafkaConsumerClient
            consumer = KafkaConsumerClient(
                bootstrap_servers=KAFKA_SERVERS,
                topics=[TOPIC],
                group_id=GROUP_ID,
            )
            await consumer.start()
            logger.info("alert_consumer_connected", attempt=attempt)
            break
        except Exception as e:
            wait = RETRY_DELAY_S * (2 ** attempt)
            logger.warning("alert_consumer_connect_failed", error=str(e), retry_in=wait)
            await asyncio.sleep(wait)
    else:
        logger.error("alert_consumer_all_retries_exhausted")
        return

    try:
        async for msg in consumer:
            try:
                raw = msg.value if isinstance(msg.value, dict) else json.loads(msg.value)
                await _process_alert_event(raw, Session)
            except Exception as e:
                logger.error("alert_consumer_process_error", error=str(e), exc_info=True)
                _CONSUMER_ERRORS.inc()
    except asyncio.CancelledError:
        logger.info("alert_consumer_cancelled")
    except Exception as e:
        logger.error("alert_consumer_fatal", error=str(e), exc_info=True)
    finally:
        try:
            await consumer.stop()
        except Exception:
            pass
        logger.info("alert_consumer_stopped")
