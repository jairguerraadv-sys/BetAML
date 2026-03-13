"""
jobs.py — Background jobs para tarefas agendadas:
  - Risk Score Decay: recalcula risk_score diariamente como weighted average dos últimos 30d
  - LGPD Data Expiration: deleta/anonimiza dados conforme data_retention_days
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, update, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal
from models import Alert, AuditLog, Notification, Player, ScoringConfig, Tenant, User

logger = structlog.get_logger(__name__)


async def _notify_admins_job_failure(job_name: str, error: str) -> None:
    """Create an in-app Notification for every ADMIN user when a scheduled job fails."""
    try:
        async with AsyncSessionLocal() as db:
            admins = (
                await db.execute(
                    select(User).where(User.role == "ADMIN", User.active == True)
                )
            ).scalars().all()
            for admin in admins:
                db.add(Notification(
                    tenant_id=admin.tenant_id,
                    user_id=admin.id,
                    type="JOB_FAILURE",
                    title=f"Falha no job agendado: {job_name}",
                    body=f"Erro: {error[:500]}",
                    reference_type="Job",
                    reference_id=job_name,
                ))
            await db.commit()
    except Exception as notify_exc:
        logger.error("job_failure_notification_error", job=job_name, error=str(notify_exc))


async def calculate_risk_score_decay() -> None:
    """
    Recalcula risk_score de cada jogador como weighted average dos últimos 30d (DECAY).

    Risk Score Decay:
      score = (rule_matches_severity_avg * 0.4) + (ml_anomaly_score_avg * 0.4) +
              (network_score_avg * 0.2)

    Onde:
      - rule_matches_severity_avg: severidade média de alertas nos últimos 30d
      - ml_anomaly_score_avg: média do anomaly_score nos últimos 30d
      - network_score_avg: média do shared_device/shared_bank no mesmo período

    Isso permite que um jogador saia de HIGH RISK se seu comportamento melhorar.
    Executa uma vez por dia (normalmente 04:00 UTC).
    """
    try:
        async with AsyncSessionLocal() as db:
            tenants = (await db.execute(select(Tenant).where(Tenant.active == True))).scalars().all()

            for tenant in tenants:
                # Buscar ScoringConfig para pesos
                scoring_cfg = (
                    await db.execute(
                        select(ScoringConfig).where(ScoringConfig.tenant_id == tenant.id).limit(1)
                    )
                ).scalar_one_or_none()

                if not scoring_cfg:
                    continue

                rule_weight = float(scoring_cfg.rule_weight or 0.4)
                ml_weight = float(scoring_cfg.ml_weight or 0.4)
                network_weight = float(scoring_cfg.network_weight or 0.2)

                # Buscar todos os players deste tenant (excluir já anonimizados)
                players = (
                    await db.execute(
                        select(Player).where(
                            Player.tenant_id == tenant.id,
                            Player.status != "ERASED",
                        )
                    )
                ).scalars().all()

                since = datetime.now(UTC) - timedelta(days=30)
                recalculated = 0

                for player in players:
                    # Alertas nos últimos 30d
                    alerts_30d = (
                        await db.execute(
                            select(Alert).where(
                                Alert.tenant_id == tenant.id,
                                Alert.player_id == player.id,
                                Alert.created_at >= since,
                            )
                        )
                    ).scalars().all()

                    if not alerts_30d:
                        # Sem alertas recentes → score decai gradualmente
                        player.risk_score = Decimal(max(float(player.risk_score or 0) * 0.95, 0))
                        recalculated += 1
                        continue

                    # Severidade média: CRITICAL=1.0, HIGH=0.75, MEDIUM=0.5, LOW=0.25
                    severity_map = {"CRITICAL": 1.0, "HIGH": 0.75, "MEDIUM": 0.5, "LOW": 0.25}
                    severity_scores = [
                        severity_map.get(a.severity, 0.5) for a in alerts_30d
                    ]
                    rule_score = sum(severity_scores) / len(severity_scores) if severity_scores else 0.0

                    # Anomaly score médio dos alerts
                    anomaly_scores = [
                        float(a.anomaly_score) for a in alerts_30d if a.anomaly_score is not None
                    ]
                    ml_score = sum(anomaly_scores) / len(anomaly_scores) if anomaly_scores else 0.0

                    # Network score (exemplo: usar shared_device_count como proxy)
                    # Isso é simplificado; em produção seria uma métrica mais sofisticada
                    network_score = min(1.0, len([a for a in alerts_30d if "shared" in (a.evidence or {})]) / 5.0)

                    # Composite score (weighted)
                    new_score = (
                        rule_score * rule_weight +
                        ml_score * ml_weight +
                        network_score * network_weight
                    )

                    # Clip e atualizar
                    player.risk_score = Decimal(str(round(min(new_score, 1.0), 4)))
                    recalculated += 1

                await db.commit()
                logger.info(
                    "risk_score_decay_completed",
                    tenant_id=tenant.id,
                    players_recalculated=recalculated,
                )
    except Exception as exc:
        logger.error("risk_score_decay_failed", error=str(exc))
        await _notify_admins_job_failure("calculate_risk_score_decay", str(exc))


async def cleanup_expired_player_data() -> None:
    """
    LGPD Data Expiration Job.

    Para cada tenant, verifica `data_retention_days` na ScoringConfig e anonimiza
    players onde `last_scored_at < NOW() - data_retention_days`.

    Anonimização:
      - CPF → ERASURE_{hash}
      - Nome → ERASURE_{hash}
      - Status → ERASED
      - Registra em audit_logs com ação LGPD_AUTO_EXPIRATION

    Executa uma vez por dia (normalmente 05:00 UTC).
    """
    try:
        async with AsyncSessionLocal() as db:
            tenants = (await db.execute(select(Tenant).where(Tenant.active == True))).scalars().all()

            for tenant in tenants:
                scoring_cfg = (
                    await db.execute(
                        select(ScoringConfig).where(ScoringConfig.tenant_id == tenant.id).limit(1)
                    )
                ).scalar_one_or_none()

                if not scoring_cfg or not scoring_cfg.data_retention_days:
                    continue

                cutoff = datetime.now(UTC) - timedelta(days=int(scoring_cfg.data_retention_days))

                # Players sem atividade no período de retenção (excluir já anonimizados)
                expired_players = (
                    await db.execute(
                        select(Player).where(
                            Player.tenant_id == tenant.id,
                            Player.status != "ERASED",
                            and_(
                                Player.last_scored_at < cutoff,
                                Player.last_scored_at.isnot(None),
                            ),
                        )
                    )
                ).scalars().all()

                erased_count = 0
                for player in expired_players:
                    # Anonimizar
                    anon_suffix = hashlib.sha256(str(player.id).encode()).hexdigest()[:12]
                    player.cpf_encrypted = f"ERASURE_{anon_suffix}".encode()
                    player.name_encrypted = f"ERASURE_{anon_suffix}".encode()
                    player.status = "ERASED"
                    erased_count += 1
                    # LGPD Art. 37 — registrar toda operação sobre dados pessoais
                    db.add(AuditLog(
                        tenant_id=tenant.id,
                        user_id=None,   # sistema
                        action="LGPD_AUTO_EXPIRATION",
                        entity_type="Player",
                        entity_id=str(player.id),
                        after={
                            "status": "ERASED",
                            "anon_suffix": anon_suffix,
                            "retention_days": int(scoring_cfg.data_retention_days),
                        },
                    ))

                await db.commit()
                logger.info(
                    "lgpd_data_expiration_completed",
                    tenant_id=tenant.id,
                    players_erased=erased_count,
                    retention_days=scoring_cfg.data_retention_days,
                )
    except Exception as exc:
        logger.error("lgpd_data_expiration_failed", error=str(exc))
        await _notify_admins_job_failure("cleanup_expired_player_data", str(exc))


# APScheduler será integrado em main.py


async def check_sla_violations() -> None:
    """
    SLA Violation Monitor — roda a cada hora.

    Verifica cases com sla_due_at < NOW() e status não-terminal (OPEN, IN_REVIEW).
    Para cada violação:
      1. Cria/atualiza Notification para o responsável e para todos os ADMINs do tenant
      2. Loga em audit_log com action="SLA_VIOLATED"
    """
    from models import Case, Notification, User
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(UTC)

            # Buscar cases vencidos não-terminais
            stmt = select(Case).where(
                Case.sla_due_at < now,
                Case.status.in_(["OPEN", "IN_REVIEW"]),
            )
            overdue_cases = (await db.execute(stmt)).scalars().all()

            if not overdue_cases:
                return

            logger.info("sla_violations_found", count=len(overdue_cases))

            for case in overdue_cases:
                # Notificar assignee (se existir)
                notif_user_ids = set()
                if case.assigned_to:
                    notif_user_ids.add(case.assigned_to)

                # Notificar todos os ADMINs do tenant
                admins = (await db.execute(
                    select(User).where(
                        User.tenant_id == case.tenant_id,
                        User.role.in_(["ADMIN", "AML_ANALYST"]),
                        User.active == True,
                    )
                )).scalars().all()
                for adm in admins:
                    notif_user_ids.add(adm.id)

                hours_overdue = int((now - case.sla_due_at).total_seconds() // 3600)

                for uid in notif_user_ids:
                    # Evita duplicatas: checa notificação recente (últimas 2h)
                    recent_cutoff = now - timedelta(hours=2)
                    existing = (await db.execute(
                        select(Notification).where(
                            Notification.tenant_id == case.tenant_id,
                            Notification.user_id == uid,
                            Notification.reference_type == "Case",
                            Notification.reference_id == str(case.id),
                            Notification.type == "SLA_VIOLATION",
                            Notification.created_at >= recent_cutoff,
                        )
                    )).scalar_one_or_none()

                    if existing:
                        continue  # já notificado recentemente

                    db.add(Notification(
                        tenant_id=case.tenant_id,
                        user_id=uid,
                        type="SLA_VIOLATION",
                        title=f"⚠️ SLA Vencido: {case.title or case.id}",
                        body=(
                            f"Case #{getattr(case, 'reference_number', case.id)} está "
                            f"{hours_overdue}h em atraso. Status: {case.status}. "
                            f"Ação imediata necessária."
                        ),
                        reference_type="Case",
                        reference_id=str(case.id),
                    ))

                # Audit log
                db.add(AuditLog(
                    tenant_id=case.tenant_id,
                    user_id=None,
                    action="SLA_VIOLATED",
                    entity_type="Case",
                    entity_id=str(case.id),
                    after={
                        "sla_due_at": case.sla_due_at.isoformat(),
                        "hours_overdue": hours_overdue,
                        "status": case.status,
                    },
                ))

            await db.commit()
            logger.info("sla_violations_processed", cases=len(overdue_cases))

    except Exception as exc:
        logger.error("check_sla_violations_failed", error=str(exc))
        await _notify_admins_job_failure("check_sla_violations", str(exc))
