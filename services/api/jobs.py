"""
jobs.py — Background jobs para tarefas agendadas:
  - Risk Score Decay: recalcula risk_score diariamente como weighted average dos últimos 30d
  - LGPD Data Expiration: deleta/anonimiza dados conforme data_retention_days
  - Feature Population Stats: computa estatísticas de população por tenant (06:00 UTC)
"""
from __future__ import annotations

import hashlib
import json
import statistics as _statistics
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, and_, text, func

from config import settings
from database import AsyncSessionLocal
from models import Alert, AuditLog, Case, FeatureSnapshot, Notification, Player, ScoringConfig, Tenant, User, FinancialTransaction, Bet, IngestError

logger = structlog.get_logger(__name__)


async def _set_db_tenant_context(db, tenant_id: object) -> None:
    """Set Postgres session variable used by RLS policies (best-effort)."""
    try:
        await db.execute(
            text("SELECT set_config('app.current_tenant', :tid, false)"),
            {"tid": str(tenant_id)},
        )
    except Exception:
        return


async def _notify_admins_job_failure(job_name: str, error: str) -> None:
    """Create an in-app Notification for every ADMIN user when a scheduled job fails."""
    try:
        async with AsyncSessionLocal() as db:
            tenants = (
                await db.execute(select(Tenant).where(Tenant.active.is_(True)))
            ).scalars().all()

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)
                admins = (
                    await db.execute(
                        select(User).where(
                            User.tenant_id == tenant.id,
                            User.role == "ADMIN",
                            User.active.is_(True),
                        )
                    )
                ).scalars().all()
                for admin in admins:
                    db.add(
                        Notification(
                            tenant_id=admin.tenant_id,
                            user_id=admin.id,
                            type="JOB_FAILURE",
                            title=f"Falha no job agendado: {job_name}",
                            body=f"Erro: {error[:500]}",
                            reference_type="Job",
                            reference_id=job_name,
                        )
                    )
                if admins:
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
            tenants = (await db.execute(select(Tenant).where(Tenant.active.is_(True)))).scalars().all()

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)
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
            tenants = (await db.execute(select(Tenant).where(Tenant.active.is_(True)))).scalars().all()

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)
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
                    player.cpf_hmac = None  # invalidar índice HMAC após erasure LGPD
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

    Verifica cases com sla_due_at < NOW() e status não-terminal
    (OPEN, INVESTIGATING, PENDING_REVIEW).

    Para cada violação:
      1. Cria Notification para o responsável e para todos os ADMINs do tenant
         (evita duplicatas: ignora se já notificado nas últimas 2h)
      2. Loga em audit_log com action="SLA_VIOLATED"

    Adicionalmente, gera notificações de aviso (SLA_WARNING) para cases
    cujo sla_due_at está dentro das próximas 2 horas.
    """
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(UTC)

            tenants = (await db.execute(select(Tenant).where(Tenant.active.is_(True)))).scalars().all()

            total_overdue = 0
            total_warn = 0

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)

                # Buscar cases vencidos não-terminais (por tenant)
                stmt = select(Case).where(
                    Case.tenant_id == tenant.id,
                    Case.sla_due_at < now,
                    Case.status.in_(["OPEN", "INVESTIGATING", "PENDING_REVIEW"]),
                )
                overdue_cases = (await db.execute(stmt)).scalars().all()
                if overdue_cases:
                    total_overdue += len(overdue_cases)

                for case in overdue_cases:
                    notif_user_ids = set()
                    if case.assigned_to:
                        notif_user_ids.add(case.assigned_to)

                    admins = (
                        await db.execute(
                            select(User).where(
                                User.tenant_id == case.tenant_id,
                                User.role.in_(["ADMIN", "AML_ANALYST"]),
                                User.active.is_(True),
                            )
                        )
                    ).scalars().all()
                    for adm in admins:
                        notif_user_ids.add(adm.id)

                    hours_overdue = int((now - case.sla_due_at).total_seconds() // 3600)

                    for uid in notif_user_ids:
                        recent_cutoff = now - timedelta(hours=2)
                        existing = (
                            await db.execute(
                                select(Notification).where(
                                    Notification.tenant_id == case.tenant_id,
                                    Notification.user_id == uid,
                                    Notification.reference_type == "Case",
                                    Notification.reference_id == str(case.id),
                                    Notification.type == "SLA_VIOLATION",
                                    Notification.created_at >= recent_cutoff,
                                )
                            )
                        ).scalar_one_or_none()
                        if existing:
                            continue
                        db.add(
                            Notification(
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
                            )
                        )

                    db.add(
                        AuditLog(
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
                        )
                    )

                if overdue_cases:
                    await db.commit()

                # ── SLA_WARNING: approaching within 2 hours (por tenant) ─────
                warn_horizon = now + timedelta(hours=2)
                approaching = (
                    await db.execute(
                        select(Case).where(
                            Case.tenant_id == tenant.id,
                            Case.sla_due_at > now,
                            Case.sla_due_at <= warn_horizon,
                            Case.status.in_(["OPEN", "INVESTIGATING", "PENDING_REVIEW"]),
                        )
                    )
                ).scalars().all()
                if approaching:
                    total_warn += len(approaching)

                for case in approaching:
                    minutes_left = int((case.sla_due_at - now).total_seconds() // 60)
                    notif_user_ids: set = set()
                    if case.assigned_to:
                        notif_user_ids.add(case.assigned_to)
                    warn_admins = (
                        await db.execute(
                            select(User).where(
                                User.tenant_id == case.tenant_id,
                                User.role.in_(["ADMIN", "AML_ANALYST"]),
                                User.active.is_(True),
                            )
                        )
                    ).scalars().all()
                    for adm in warn_admins:
                        notif_user_ids.add(adm.id)

                    for uid in notif_user_ids:
                        warn_cutoff = now - timedelta(hours=1)
                        existing_warn = (
                            await db.execute(
                                select(Notification).where(
                                    Notification.tenant_id == case.tenant_id,
                                    Notification.user_id == uid,
                                    Notification.reference_type == "Case",
                                    Notification.reference_id == str(case.id),
                                    Notification.type == "SLA_WARNING",
                                    Notification.created_at >= warn_cutoff,
                                )
                            )
                        ).scalar_one_or_none()
                        if existing_warn:
                            continue
                        db.add(
                            Notification(
                                tenant_id=case.tenant_id,
                                user_id=uid,
                                type="SLA_WARNING",
                                title=f"SLA a vencer: {case.title or case.id}",
                                body=f"Caso vence em {minutes_left} minutos. Status atual: {case.status}.",
                                reference_type="Case",
                                reference_id=str(case.id),
                            )
                        )

                if approaching:
                    await db.commit()

            if total_overdue:
                logger.info("sla_violations_processed", cases=total_overdue)
            if total_warn:
                logger.info("sla_warnings_sent", cases=total_warn)

    except Exception as exc:
        logger.error("check_sla_violations_failed", error=str(exc))
        await _notify_admins_job_failure("check_sla_violations", str(exc))


async def compute_feature_population_stats() -> None:
    """
    Feature Population Stats Job.

    For each active tenant, queries the last 30 days of feature_snapshots and
    computes population-level statistics (mean, std, p10, p25, p50, p75, p90)
    for every numeric feature column found in the JSONB `features` field.

    Results are stored in Redis:
      Key:   feature_stats:{tenant_id}
      Value: JSON dict {"deposit_sum_30d": {"mean": ..., "std": ..., "p50": ..., ...}, ...}
      TTL:   25 hours (refreshed each run)

    These statistics are consumed by eval_dsl's zscore() and percentile_rank() functions.
    Runs once per day at 06:00 UTC.
    """
    try:
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)

        try:
            async with AsyncSessionLocal() as db:
                tenants = (
                    await db.execute(select(Tenant).where(Tenant.active.is_(True)))
                ).scalars().all()

                since = datetime.now(UTC) - timedelta(days=30)

                for tenant in tenants:
                    await _set_db_tenant_context(db, tenant.id)
                    snapshots = (
                        await db.execute(
                            select(FeatureSnapshot).where(
                                FeatureSnapshot.tenant_id == tenant.id,
                                FeatureSnapshot.created_at >= since,
                            )
                        )
                    ).scalars().all()

                    if not snapshots:
                        continue

                    # Accumulate numeric values per feature key
                    feature_values: dict[str, list[float]] = {}
                    for snap in snapshots:
                        for key, raw_val in (snap.features or {}).items():
                            # Skip non-numeric and sentinel values
                            if raw_val is None or raw_val == "" or isinstance(raw_val, bool):
                                continue
                            try:
                                val = float(raw_val)
                            except (TypeError, ValueError):
                                continue
                            feature_values.setdefault(key, []).append(val)

                    def _percentile(sorted_list: list[float], p: float) -> float:
                        """Linear interpolation percentile over a pre-sorted list."""
                        n = len(sorted_list)
                        if n == 1:
                            return sorted_list[0]
                        idx = (p / 100.0) * (n - 1)
                        lo = int(idx)
                        hi = min(lo + 1, n - 1)
                        frac = idx - lo
                        return sorted_list[lo] * (1.0 - frac) + sorted_list[hi] * frac

                    stats_out: dict[str, dict] = {}
                    for feat_name, vals in feature_values.items():
                        if not vals:
                            continue
                        sorted_vals = sorted(vals)
                        n = len(sorted_vals)
                        stats_out[feat_name] = {
                            "mean":  round(_statistics.mean(vals), 4),
                            "std":   round(_statistics.pstdev(vals), 4) if n > 1 else 0.0,
                            "p10":   round(_percentile(sorted_vals, 10), 4),
                            "p25":   round(_percentile(sorted_vals, 25), 4),
                            "p50":   round(_percentile(sorted_vals, 50), 4),
                            "p75":   round(_percentile(sorted_vals, 75), 4),
                            "p90":   round(_percentile(sorted_vals, 90), 4),
                            "count": n,
                        }

                    redis_key = f"feature_stats:{tenant.id}"
                    await redis_client.set(
                        redis_key,
                        json.dumps(
                            {
                                "computed_at": datetime.now(UTC).isoformat(),
                                "features": stats_out,
                            },
                            ensure_ascii=False,
                        ),
                        ex=25 * 3600,   # 25h TTL — outlasts a missed daily run
                    )

                    logger.info(
                        "feature_population_stats_computed",
                        tenant_id=tenant.id,
                        features_computed=len(stats_out),
                        snapshots_processed=len(snapshots),
                    )
        finally:
            await redis_client.aclose()

    except Exception as exc:
        logger.error("compute_feature_population_stats_failed", error=str(exc))
        await _notify_admins_job_failure("compute_feature_population_stats", str(exc))


async def clickhouse_backfill_features_daily() -> None:
    """Backfill diário do ClickHouse (Gold) a partir do Postgres.

    Copia `feature_snapshots` do dia anterior (UTC) para `betaml.player_features_daily`.
    A tabela em ClickHouse é ReplacingMergeTree(computed_at); inserir novamente o mesmo
    dia é seguro, e `FINAL` no reader garante deduplicação imediata.
    """
    try:
        from libs.clients import ClickHouseClient

        target_date = (datetime.now(UTC).date() - timedelta(days=1))
        ch = ClickHouseClient(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            database=settings.clickhouse_db,
        )

        async with AsyncSessionLocal() as db:
            tenants = (
                await db.execute(select(Tenant).where(Tenant.active.is_(True)))
            ).scalars().all()

            total_rows = 0
            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)

                snaps = (
                    await db.execute(
                        select(FeatureSnapshot).where(
                            FeatureSnapshot.tenant_id == tenant.id,
                            FeatureSnapshot.feature_date == target_date,
                        )
                    )
                ).scalars().all()

                if not snaps:
                    continue

                q2 = Decimal("0.01")
                q4 = Decimal("0.0001")

                def _to_dec(v: object, q: Decimal) -> Decimal:
                    try:
                        if v is None or v == "":
                            return Decimal("0").quantize(q)
                        if isinstance(v, Decimal):
                            return v.quantize(q)
                        return Decimal(str(v)).quantize(q)
                    except Exception:
                        return Decimal("0").quantize(q)

                def _to_int(v: object, default: int = 0) -> int:
                    try:
                        if v is None or v == "":
                            return default
                        return int(float(v))
                    except Exception:
                        return default

                rows: list[dict[str, object]] = []
                for snap in snaps:
                    feats = snap.features or {}
                    created_at = snap.created_at
                    if hasattr(created_at, "tzinfo") and created_at.tzinfo is not None:
                        created_at = created_at.astimezone(UTC).replace(tzinfo=None)

                    rows.append(
                        {
                            "tenant_id": str(snap.tenant_id),
                            "player_id": str(snap.player_id),
                            "feature_date": snap.snapshot_date_value,
                            "deposit_sum_24h": _to_dec(feats.get("deposit_sum_24h"), q2),
                            "deposit_sum_7d": _to_dec(feats.get("deposit_sum_7d"), q2),
                            "deposit_sum_30d": _to_dec(feats.get("deposit_sum_30d"), q2),
                            "deposit_count_24h": _to_int(feats.get("deposit_count_24h")),
                            "deposit_count_7d": _to_int(feats.get("deposit_count_7d")),
                            "withdrawal_sum_24h": _to_dec(feats.get("withdrawal_sum_24h"), q2),
                            "withdrawal_sum_7d": _to_dec(feats.get("withdrawal_sum_7d"), q2),
                            "withdrawal_count_24h": _to_int(feats.get("withdrawal_count_24h")),
                            "bet_stake_sum_24h": _to_dec(feats.get("bet_stake_sum_24h"), q2),
                            "bet_stake_sum_7d": _to_dec(feats.get("bet_stake_sum_7d"), q2),
                            "ratio_w2d_7d": _to_dec(feats.get("ratio_withdrawal_to_deposit_7d"), q4),
                            "baseline_avg_deposit": _to_dec(feats.get("baseline_avg_daily_deposit"), q2),
                            "baseline_stddev_deposit": _to_dec(feats.get("baseline_stddev_deposit"), q2),
                            "zscore_deposit": _to_dec(feats.get("zscore_current_deposit_vs_baseline"), q4),
                            "new_payment_flag": int(bool(feats.get("new_payment_instrument_flag", False))),
                            "new_device_flag": int(bool(feats.get("new_device_flag", False))),
                            "shared_device_count": _to_int(feats.get("shared_device_count")),
                            "shared_bank_count": _to_int(feats.get("shared_bank_account_count")),
                            "chargeback_count_30d": _to_int(feats.get("chargeback_count_30d")),
                            "deposit_velocity": _to_dec(feats.get("deposit_velocity"), q4),
                            "unique_instruments_7d": _to_int(feats.get("unique_instruments_7d")),
                            "night_activity_ratio": _to_dec(feats.get("night_activity_ratio"), q4),
                            "weekend_activity_ratio": _to_dec(feats.get("weekend_activity_ratio"), q4),
                            "avg_odds_bet_7d": _to_dec(feats.get("avg_odds_bet_7d"), q4),
                            "win_loss_ratio_30d": _to_dec(feats.get("win_loss_ratio_30d"), q4),
                            "avg_dep_to_wdraw_hours": _to_dec(feats.get("avg_deposit_to_withdrawal_hours"), q4),
                            "inconsistent_currency_flag": int(bool(feats.get("inconsistent_currency_flag", False))),
                            "chargeback_rate_30d": _to_dec(feats.get("chargeback_rate_30d"), q4),
                            "bonus_to_real_ratio_30d": _to_dec(feats.get("bonus_to_real_ratio_30d"), q4),
                            "cashout_ratio_7d": _to_dec(feats.get("cashout_ratio_7d"), q4),
                            "shared_instrument_score": _to_dec(feats.get("shared_instrument_score"), q4),
                            "feature_version": int(getattr(snap, "feature_version", None) or feats.get("feature_version", 2) or 2),
                            "computed_at": created_at,
                        }
                    )

                # Inserção em batch no ClickHouse (sync → thread)
                batch_size = 1000
                for i in range(0, len(rows), batch_size):
                    batch = rows[i : i + batch_size]
                    await asyncio.to_thread(ch.insert_dict, "betaml.player_features_daily", batch)

                total_rows += len(rows)
                logger.info(
                    "clickhouse_backfill_features_tenant_done",
                    tenant_id=str(tenant.id),
                    feature_date=str(target_date),
                    rows=len(rows),
                )

        logger.info(
            "clickhouse_backfill_features_daily_done",
            feature_date=str(target_date),
            rows=total_rows,
        )
    except Exception as exc:
        logger.error("clickhouse_backfill_features_daily_failed", error=str(exc))
        await _notify_admins_job_failure("clickhouse_backfill_features_daily", str(exc))


async def data_quality_alerting() -> None:
    """Executa checks de qualidade de dados e cria Notifications em caso de falha.

    Implementação minimalista (sem Great Expectations):
    - Executa checks por tenant (compatível com FORCE RLS).
    - Se falhas: cria Notification para ADMINs.
    - Se falhas críticas: marca pause de ingest em Tenant.settings.
    """
    job_name = "data_quality_alerting"
    try:
        async with AsyncSessionLocal() as db:
            tenants = (
                await db.execute(select(Tenant).where(Tenant.active.is_(True)))
            ).scalars().all()

            now_utc = datetime.now(UTC)
            cutoff_24h = now_utc - timedelta(hours=24)
            dedup_since = now_utc - timedelta(hours=24)

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)

                failures: list[dict[str, object]] = []
                critical = False

                invalid_statuses = ("OPEN", "IN_REVIEW", "CLOSED", "FALSE_POSITIVE")
                alerts_invalid_status = (
                    await db.execute(
                        select(func.count()).select_from(Alert).where(
                            Alert.tenant_id == tenant.id,
                            ~Alert.status.in_(invalid_statuses),
                        )
                    )
                ).scalar_one()
                if int(alerts_invalid_status or 0) > 0:
                    failures.append(
                        {
                            "name": "alerts_invalid_status",
                            "value": int(alerts_invalid_status),
                            "threshold": 0,
                            "details": "Alertas devem ter status válido.",
                        }
                    )

                snapshots_missing_version = (
                    await db.execute(
                        select(func.count()).select_from(FeatureSnapshot).where(
                            FeatureSnapshot.tenant_id == tenant.id,
                            FeatureSnapshot.feature_version.is_(None),
                        )
                    )
                ).scalar_one()
                if int(snapshots_missing_version or 0) > 0:
                    failures.append(
                        {
                            "name": "feature_snapshots_missing_version",
                            "value": int(snapshots_missing_version),
                            "threshold": 0,
                            "details": "Snapshots de features devem ter feature_version.",
                        }
                    )
                    critical = True

                unresolved_ingest_errors_24h = (
                    await db.execute(
                        select(func.count()).select_from(IngestError).where(
                            IngestError.tenant_id == tenant.id,
                            IngestError.resolved.is_(False),
                            IngestError.created_at < cutoff_24h,
                        )
                    )
                ).scalar_one()
                if int(unresolved_ingest_errors_24h or 0) > 100:
                    failures.append(
                        {
                            "name": "unresolved_ingest_errors_24h",
                            "value": int(unresolved_ingest_errors_24h),
                            "threshold": 100,
                            "details": "Ingest errors antigos não resolvidos devem ficar abaixo de 100.",
                        }
                    )
                    critical = True

                if not failures:
                    continue

                admins = (
                    await db.execute(
                        select(User).where(
                            User.tenant_id == tenant.id,
                            User.role == "ADMIN",
                            User.active.is_(True),
                        )
                    )
                ).scalars().all()

                body = {
                    "tenant_id": str(tenant.id),
                    "run_at": now_utc.isoformat(),
                    "failures": failures,
                    "critical": critical,
                }

                # Dedup fingerprint: mesma combinação de falhas (por tenant)
                # gera o mesmo reference_id, evitando spam em execuções repetidas.
                fingerprint_src = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
                fingerprint = hashlib.sha256(fingerprint_src).hexdigest()
                reference_id = f"dq:{fingerprint}"

                wrote_anything = False

                for admin in admins:
                    existing = (
                        await db.execute(
                            select(Notification).where(
                                Notification.tenant_id == tenant.id,
                                Notification.user_id == admin.id,
                                Notification.type == "DQ_ALERT",
                                Notification.reference_type == "DataQuality",
                                Notification.reference_id == reference_id,
                                Notification.created_at >= dedup_since,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing:
                        continue

                    db.add(
                        Notification(
                            tenant_id=tenant.id,
                            user_id=admin.id,
                            type="DQ_ALERT",
                            title="Data Quality: falhas detectadas",
                            body=json.dumps(body, ensure_ascii=False)[:2000],
                            reference_type="DataQuality",
                            reference_id=reference_id,
                        )
                    )
                    wrote_anything = True

                if critical:
                    try:
                        settings_json = tenant.settings or {}
                        if not isinstance(settings_json, dict):
                            settings_json = {}
                        if settings_json.get("ingest_paused") is not True:
                            tenant.settings = {
                                **settings_json,
                                "ingest_paused": True,
                                "ingest_paused_reason": "data_quality_critical",
                                "ingest_paused_at": now_utc.isoformat(),
                            }
                            wrote_anything = True
                    except Exception:
                        # best-effort: não bloqueia criação de Notification
                        pass

                if wrote_anything:
                    await db.commit()

                logger.warning(
                    "data_quality_failures_detected",
                    tenant_id=str(tenant.id),
                    failures=len(failures),
                    critical=critical,
                )
    except Exception as exc:
        logger.error("data_quality_alerting_failed", error=str(exc))
        await _notify_admins_job_failure(job_name, str(exc))


async def data_retention_batch() -> None:
    """
    Data Retention Batch — runs weekly (Sunday 03:00 UTC).

    For each active tenant, reads data_retention_raw_years and data_retention_gold_years
    from ScoringConfig (defaults: raw=5yr, gold=3yr) and:
      - Nullifies raw_payload in FinancialTransaction + Bet older than raw_cutoff
      - Deletes IngestError records older than raw_cutoff
      - Deletes FeatureSnapshot records older than gold_cutoff

    AuditLog records are never purged (permanent compliance archive).
    """
    from sqlalchemy import update as sa_update, delete as sa_delete

    try:
        async with AsyncSessionLocal() as db:
            tenants = (await db.execute(select(Tenant).where(Tenant.active.is_(True)))).scalars().all()

            for tenant in tenants:
                await _set_db_tenant_context(db, tenant.id)
                sc = (await db.execute(
                    select(ScoringConfig).where(ScoringConfig.tenant_id == tenant.id).limit(1)
                )).scalar_one_or_none()

                raw_years  = int(getattr(sc, "data_retention_raw_years",  None) or 5)
                gold_years = int(getattr(sc, "data_retention_gold_years", None) or 3)
                raw_cutoff  = datetime.now(UTC) - timedelta(days=raw_years  * 365)
                gold_cutoff = datetime.now(UTC) - timedelta(days=gold_years * 365)

                await db.execute(
                    sa_update(FinancialTransaction)
                    .where(
                        FinancialTransaction.tenant_id == tenant.id,
                        FinancialTransaction.occurred_at < raw_cutoff,
                    )
                    .values(raw_payload={})
                )
                await db.execute(
                    sa_update(Bet)
                    .where(
                        Bet.tenant_id == tenant.id,
                        Bet.occurred_at < raw_cutoff,
                    )
                    .values(raw_payload={})
                )
                await db.execute(
                    sa_delete(IngestError)
                    .where(
                        IngestError.tenant_id == tenant.id,
                        IngestError.created_at < raw_cutoff,
                    )
                )
                await db.execute(
                    sa_delete(FeatureSnapshot)
                    .where(
                        FeatureSnapshot.tenant_id == tenant.id,
                        FeatureSnapshot.created_at < gold_cutoff,
                    )
                )
                await db.commit()
                logger.info(
                    "data_retention_batch_completed",
                    tenant_id=tenant.id,
                    raw_cutoff=raw_cutoff.isoformat(),
                    gold_cutoff=gold_cutoff.isoformat(),
                )

    except Exception as exc:
        logger.error("data_retention_batch_failed", error=str(exc))
        await _notify_admins_job_failure("data_retention_batch", str(exc))
