"""
routers/stats.py — Pre-aggregated dashboard statistics.

Returns KPI counts for the current tenant in a single DB round-trip,
replacing client-side aggregation over 500-record paginated lists.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import case as sqla_case, cast, Float, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import get_current_user, require_roles
from database import get_db
from models import (
    Alert, Case, CaseEvent, FeatureSnapshot, IngestError, IngestJob,
    ModelInferenceLog, Player, ReportPackage, RuleDefinition, User,
)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard", summary="Dashboard KPI counts")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns pre-aggregated counts for the dashboard KPI cards.
    Single DB call — avoids client-side filtering of paginated lists.
    """
    tid = current_user.tenant_id
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    since_30d = today_start - timedelta(days=29)
    since_7d = today_start - timedelta(days=6)
    _closed = ("CLOSED", "REPORTED", "ARCHIVED")

    # All counts in one round-trip using scalar subqueries
    alerts_today = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.created_at >= today_start,
        )
    )).scalar_one()

    critical_open = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.severity == "CRITICAL",
            Alert.status == "OPEN",
        )
    )).scalar_one()

    cases_open = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.in_(("OPEN", "IN_REVIEW", "INVESTIGATING", "PENDING_REVIEW")),
        )
    )).scalar_one()

    sla_expired = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.sla_due_at != None,  # noqa: E711
            Case.sla_due_at < now,
            Case.status.notin_(_closed),
        )
    )).scalar_one()

    auto_detected = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.auto_created == True,  # noqa: E712
        )
    )).scalar_one()

    alerts_open = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.status == "OPEN",
        )
    )).scalar_one()

    cases_investigating = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.in_(("INVESTIGATING", "PENDING_REVIEW", "IN_REVIEW")),
        )
    )).scalar_one()

    cases_near_sla = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.sla_due_at != None,  # noqa: E711
            Case.sla_due_at >= now,
            Case.sla_due_at < now + timedelta(hours=24),
            Case.status.notin_(_closed),
        )
    )).scalar_one()

    high_risk_players = (await db.execute(
        select(func.count(Player.id)).where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
            Player.risk_band == "HIGH",
        )
    )).scalar_one()

    events_ingested_today = int((await db.execute(
        select(func.coalesce(func.sum(IngestJob.processed_records), 0)).where(
            IngestJob.tenant_id == tid,
            IngestJob.created_at >= today_start,
            IngestJob.created_at < tomorrow,
        )
    )).scalar_one() or 0)

    # Per-severity open alert counts
    sev_rows = (await db.execute(
        select(Alert.severity, func.count(Alert.id))
        .where(Alert.tenant_id == tid, Alert.status == "OPEN")
        .group_by(Alert.severity)
    )).all()
    by_severity = {row[0]: row[1] for row in sev_rows}

    recent_alert_rows = (await db.execute(
        select(Alert.created_at, Alert.severity, Alert.alert_type).where(
            Alert.tenant_id == tid,
            Alert.created_at >= since_30d,
        )
    )).all()

    timeline_map = {
        (today_start - timedelta(days=offset)).date().isoformat(): {
            "date": (today_start - timedelta(days=offset)).date().isoformat(),
            "CRITICAL": 0,
            "HIGH": 0,
            "MEDIUM": 0,
            "LOW": 0,
            "total": 0,
        }
        for offset in range(29, -1, -1)
    }
    rule_type_counter: Counter[str] = Counter()
    heatmap_map: dict[tuple[int, int], int] = {}

    for created_at, severity, alert_type in recent_alert_rows:
        if not created_at:
            continue
        key = created_at.date().isoformat()
        if key in timeline_map:
            bucket = timeline_map[key]
            sev = str(severity or "").upper()
            if sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
                bucket[sev] += 1
            bucket["total"] += 1
        rule_type_counter[str(alert_type or "UNKNOWN")] += 1
        weekday = int(created_at.weekday())
        hour = int(created_at.hour)
        heatmap_map[(weekday, hour)] = heatmap_map.get((weekday, hour), 0) + 1

    top_players_rows = (await db.execute(
        select(Player.id, Player.external_player_id, Player.risk_score, Player.risk_band).where(
            Player.tenant_id == tid,
            Player.status != "ERASED",
        )
        .order_by(Player.risk_score.desc(), Player.updated_at.desc())
        .limit(10)
    )).all()

    payload: dict = {
        "generated_at": now,
        "alerts_today":  alerts_today,
        "critical_open": critical_open,
        "cases_open":    cases_open,
        "sla_expired":   sla_expired,
        "auto_detected": auto_detected,
        "by_severity":   by_severity,
        "alerts_open": alerts_open,
        "cases_investigating": cases_investigating,
        "cases_near_sla": cases_near_sla,
        "high_risk_players": high_risk_players,
        "events_ingested_today": events_ingested_today,
        "alerts_by_severity_30d": list(timeline_map.values()),
        "alerts_by_rule_type": [
            {"label": label, "value": count}
            for label, count in rule_type_counter.most_common(10)
        ],
        "top_players_by_risk": [
            {
                "player_id": str(row[0]),
                "external_player_id": row[1],
                "risk_score": float(row[2] or 0),
                "risk_band": row[3] or "UNKNOWN",
            }
            for row in top_players_rows
        ],
        "alert_heatmap": [
            {"weekday": weekday, "hour": hour, "count": count}
            for (weekday, hour), count in sorted(heatmap_map.items())
        ],
    }

    # ── Analyst-specific KPIs ─────────────────────────────────────────────────

    dismissed_7d = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.status == "DISMISSED",
            Alert.updated_at >= since_7d,
        )
    )).scalar_one()

    my_cases_near_sla = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.assigned_to == str(current_user.id),
            Case.sla_due_at != None,  # noqa: E711
            Case.sla_due_at >= now,
            Case.sla_due_at < now + timedelta(hours=24),
            Case.status.notin_(_closed),
        )
    )).scalar_one()

    # Rules with the most false-positive-labelled alerts in the last 30 days
    fp_by_rule_rows = (await db.execute(
        select(Alert.rule_id, func.count(Alert.id).label("fp_count"))
        .where(
            Alert.tenant_id == tid,
            Alert.label == "FALSE_POSITIVE",
            Alert.created_at >= since_30d,
            Alert.rule_id != None,  # noqa: E711
        )
        .group_by(Alert.rule_id)
        .order_by(func.count(Alert.id).desc())
        .limit(3)
    )).all()

    rule_ids = [str(r[0]) for r in fp_by_rule_rows if r[0]]
    rule_names: dict[str, str] = {}
    if rule_ids:
        name_rows = (await db.execute(
            select(RuleDefinition.id, RuleDefinition.name).where(RuleDefinition.id.in_(rule_ids))
        )).all()
        rule_names = {str(r[0]): r[1] for r in name_rows}

    high_fp_rules = [
        {
            "rule_id": str(r[0]),
            "rule_name": rule_names.get(str(r[0]), "Regra desconhecida"),
            "fp_count": r[1],
        }
        for r in fp_by_rule_rows
    ]

    payload["dismissed_7d"] = dismissed_7d
    payload["my_cases_near_sla"] = my_cases_near_sla
    payload["high_fp_rules"] = high_fp_rules
    return payload


# ── PLD KPI endpoint ──────────────────────────────────────────────────────────

@router.get("/pld-kpis", summary="KPIs PLD/FT para analista de compliance")
async def pld_kpis(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """KPIs operacionais do analista de PLD/FT — métricas que realmente importam.

    Responde em uma única chamada ao banco com:
    - Funil de comunicação COAF (alertas → casos → RIF enviados)
    - Taxa de encerramento e tempo médio de resolução de casos (30d)
    - Eficiência de detecção: precision (TP/FP ratio) dos últimos 30d
    - Conformidade de SLA: percentual de casos encerrados dentro do prazo
    - Carga por analista (ADMIN/AML_ANALYST)
    - Regras com mais acionamentos e mais falsos positivos (30d)
    """
    tid = current_user.tenant_id
    now = datetime.now(UTC)
    since_30d = now - timedelta(days=30)
    since_7d  = now - timedelta(days=7)
    _closed   = ("CLOSED", "REPORTED", "ARCHIVED")

    # ── Funil COAF ────────────────────────────────────────────────────────────
    alerts_total_30d = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.created_at >= since_30d,
        )
    )).scalar_one()

    alerts_escalated_30d = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.case_id.isnot(None),
            Alert.created_at >= since_30d,
        )
    )).scalar_one()

    cases_total_30d = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.created_at >= since_30d,
        )
    )).scalar_one()

    reports_submitted_30d = (await db.execute(
        select(func.count(ReportPackage.id)).where(
            ReportPackage.tenant_id == tid,
            ReportPackage.status == "SUBMITTED",
            ReportPackage.created_at >= since_30d,
        )
    )).scalar_one()

    reports_pending = (await db.execute(
        select(func.count(ReportPackage.id)).where(
            ReportPackage.tenant_id == tid,
            ReportPackage.status.in_(["DRAFT", "IN_REVIEW"]),
        )
    )).scalar_one()

    # ── Encerramento de casos 30d ─────────────────────────────────────────────
    cases_closed_30d = (await db.execute(
        select(func.count(Case.id)).where(
            Case.tenant_id == tid,
            Case.status.in_(_closed),
            Case.closed_at.isnot(None),
            Case.closed_at >= since_30d,
        )
    )).scalar_one()

    closure_rate = (
        round(cases_closed_30d / cases_total_30d, 4) if cases_total_30d else None
    )

    # Tempo médio de resolução: closed_at - created_at em horas
    resolution_rows = (await db.execute(
        select(Case.created_at, Case.closed_at).where(
            Case.tenant_id == tid,
            Case.status.in_(_closed),
            Case.closed_at.isnot(None),
            Case.created_at.isnot(None),
            Case.closed_at >= since_30d,
        )
    )).all()
    resolution_hours: list[float] = []
    for created_at, closed_at in resolution_rows:
        if created_at and closed_at:
            delta = (closed_at - created_at).total_seconds() / 3600
            if delta >= 0:
                resolution_hours.append(delta)
    avg_resolution_hours = (
        round(sum(resolution_hours) / len(resolution_hours), 2) if resolution_hours else None
    )

    # ── Precisão de detecção (30d) ────────────────────────────────────────────
    tp_count = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.label == "TRUE_POSITIVE",
            Alert.created_at >= since_30d,
        )
    )).scalar_one()

    fp_count = (await db.execute(
        select(func.count(Alert.id)).where(
            Alert.tenant_id == tid,
            Alert.label == "FALSE_POSITIVE",
            Alert.created_at >= since_30d,
        )
    )).scalar_one()

    labeled_count = tp_count + fp_count
    precision_30d = round(tp_count / labeled_count, 4) if labeled_count else None

    # ── Conformidade de SLA ───────────────────────────────────────────────────
    with_sla_rows = (await db.execute(
        select(Case.sla_due_at, Case.closed_at).where(
            Case.tenant_id == tid,
            Case.status.in_(_closed),
            Case.closed_at.isnot(None),
            Case.sla_due_at.isnot(None),
            Case.closed_at >= since_30d,
        )
    )).all()
    on_time = sum(1 for sla, closed in with_sla_rows if closed <= sla)
    sla_compliance_rate = (
        round(on_time / len(with_sla_rows), 4) if with_sla_rows else None
    )

    # ── Top regras acionadas 30d ──────────────────────────────────────────────
    top_rules_rows = (await db.execute(
        select(Alert.rule_id, func.count(Alert.id).label("fire_count"))
        .where(
            Alert.tenant_id == tid,
            Alert.rule_id.isnot(None),
            Alert.created_at >= since_30d,
        )
        .group_by(Alert.rule_id)
        .order_by(func.count(Alert.id).desc())
        .limit(5)
    )).all()

    rule_ids_top = [str(r[0]) for r in top_rules_rows if r[0]]
    rule_names_top: dict[str, str] = {}
    if rule_ids_top:
        name_rows = (await db.execute(
            select(RuleDefinition.id, RuleDefinition.name).where(
                RuleDefinition.id.in_(rule_ids_top)
            )
        )).all()
        rule_names_top = {str(r[0]): r[1] for r in name_rows}

    top_triggered_rules = [
        {
            "rule_id": str(r[0]),
            "rule_name": rule_names_top.get(str(r[0]), "Desconhecida"),
            "fire_count_30d": r[1],
        }
        for r in top_rules_rows
    ]

    # ── ML eficácia: anomaly score médio nos últimos 7d ───────────────────────
    ml_score_row = (await db.execute(
        select(
            func.avg(ModelInferenceLog.anomaly_score).label("avg_score"),
            func.count(ModelInferenceLog.id).label("inferences"),
            func.sum(sqla_case((ModelInferenceLog.is_anomaly == True, 1), else_=0)).label("anomalies"),  # noqa: E712
        ).where(
            ModelInferenceLog.tenant_id == tid,
            ModelInferenceLog.created_at >= since_7d,
        )
    )).one()

    # ── Carga por analista (ADMIN/AML_ANALYST only) ───────────────────────────
    analyst_workload: list[dict] = []
    if current_user.role in ("ADMIN", "AML_ANALYST"):
        workload_rows = (await db.execute(
            select(
                Case.assigned_to,
                func.count(Case.id).label("open_cases"),
                func.count(
                    sqla_case(
                        (
                            (Case.sla_due_at.isnot(None))
                            & (Case.sla_due_at >= now)
                            & (Case.sla_due_at < now + timedelta(hours=24)),
                            Case.id,
                        ),
                        else_=None,
                    )
                ).label("near_sla"),
            )
            .where(
                Case.tenant_id == tid,
                Case.assigned_to.isnot(None),
                Case.status.notin_(_closed),
            )
            .group_by(Case.assigned_to)
            .order_by(func.count(Case.id).desc())
        )).all()

        analyst_workload = [
            {
                "assigned_to": str(r[0]),
                "open_cases": r[1],
                "near_sla": r[2],
            }
            for r in workload_rows
        ]

    return {
        "generated_at": now,
        "period_days": 30,
        "coaf_funnel": {
            "alerts_total": alerts_total_30d,
            "alerts_escalated_to_case": alerts_escalated_30d,
            "cases_opened": cases_total_30d,
            "reports_submitted": reports_submitted_30d,
            "reports_pending": reports_pending,
            "escalation_rate": (
                round(alerts_escalated_30d / alerts_total_30d, 4) if alerts_total_30d else None
            ),
            "filing_rate": (
                round(reports_submitted_30d / cases_total_30d, 4) if cases_total_30d else None
            ),
        },
        "case_resolution": {
            "closed_30d": cases_closed_30d,
            "closure_rate": closure_rate,
            "avg_resolution_hours": avg_resolution_hours,
            "sla_compliance_rate": sla_compliance_rate,
        },
        "detection_quality": {
            "true_positive_30d": tp_count,
            "false_positive_30d": fp_count,
            "labeled_30d": labeled_count,
            "precision_30d": precision_30d,
        },
        "ml_inference_7d": {
            "total_inferences": int(ml_score_row.inferences or 0),
            "anomalies_flagged": int(ml_score_row.anomalies or 0),
            "avg_anomaly_score": float(ml_score_row.avg_score or 0),
        },
        "top_triggered_rules_30d": top_triggered_rules,
        "analyst_workload": analyst_workload,
    }


# ── Data quality dashboard ────────────────────────────────────────────────────

@router.get("/data-quality", summary="Dashboard de qualidade de dados")
async def data_quality(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Dashboard de qualidade de dados para o analista de PLD.

    Consolida em uma única chamada:
    - Null ratios por feature (últimos 7d — feature_snapshots)
    - Drift score médio/máximo por feature (últimos 7d)
    - Erros de ingestão por source_system (últimos 30d)
    - Frescor dos dados: último registro ingerido por source_system
    - Jobs com taxa de erro > 10% nos últimos 7d
    """
    tid = current_user.tenant_id
    now = datetime.now(UTC)
    since_7d  = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    # ── Feature null ratios e drift (últimos 7d) ──────────────────────────────
    snaps = (await db.execute(
        select(FeatureSnapshot.features, FeatureSnapshot.drift_score).where(
            FeatureSnapshot.tenant_id == tid,
            FeatureSnapshot.created_at >= since_7d,
        ).limit(2000)  # cap para evitar OOM
    )).all()

    feature_stats: dict[str, dict] = {}
    for row_features, drift_score in snaps:
        for key, value in (row_features or {}).items():
            s = feature_stats.setdefault(key, {"null": 0, "total": 0, "drifts": []})
            s["total"] += 1
            if value in (None, "", "null"):
                s["null"] += 1
        if drift_score is not None:
            # Drift score é por snapshot, não por feature — atribuímos ao bucket geral
            feature_stats.setdefault("__drift__", {"null": 0, "total": 0, "drifts": []})
            feature_stats["__drift__"]["drifts"].append(float(drift_score))

    null_ratio_alerts: list[dict] = []
    feature_null_summary: list[dict] = []
    for key, s in sorted(feature_stats.items()):
        if key == "__drift__":
            continue
        null_ratio = round(s["null"] / s["total"], 4) if s["total"] else 0.0
        entry = {"feature": key, "null_ratio": null_ratio, "sample_count": s["total"]}
        feature_null_summary.append(entry)
        if null_ratio >= 0.10:  # threshold: >10% nulos é alerta
            null_ratio_alerts.append({**entry, "severity": "HIGH" if null_ratio >= 0.30 else "MEDIUM"})

    drift_scores = feature_stats.get("__drift__", {}).get("drifts", [])
    drift_summary = {
        "sample_count": len(drift_scores),
        "avg_drift_score": round(sum(drift_scores) / len(drift_scores), 4) if drift_scores else None,
        "max_drift_score": round(max(drift_scores), 4) if drift_scores else None,
        "snapshots_with_drift": sum(1 for d in drift_scores if d >= 0.3),
    }

    # ── Erros de ingestão por source_system (30d) ─────────────────────────────
    ingest_err_rows = (await db.execute(
        select(IngestError.source_system, func.count(IngestError.id).label("error_count"))
        .where(
            IngestError.tenant_id == tid,
            IngestError.created_at >= since_30d,
        )
        .group_by(IngestError.source_system)
        .order_by(func.count(IngestError.id).desc())
        .limit(20)
    )).all()

    ingest_errors_by_source = [
        {"source_system": row[0], "error_count_30d": row[1]}
        for row in ingest_err_rows
    ]

    # ── Frescor: último registro ingerido por source_system ──────────────────
    freshness_rows = (await db.execute(
        select(IngestJob.source_system, func.max(IngestJob.updated_at).label("last_updated"))
        .where(
            IngestJob.tenant_id == tid,
            IngestJob.status == "COMPLETED",
        )
        .group_by(IngestJob.source_system)
    )).all()

    data_freshness: list[dict] = []
    for source_system, last_updated in freshness_rows:
        staleness_hours = (
            round((now - last_updated).total_seconds() / 3600, 1) if last_updated else None
        )
        data_freshness.append({
            "source_system": source_system,
            "last_completed_job": last_updated,
            "staleness_hours": staleness_hours,
            "stale": staleness_hours is not None and staleness_hours > 24,
        })
    data_freshness.sort(key=lambda x: (x["stale"] is True, x.get("staleness_hours") or 0), reverse=True)

    # ── Jobs com alta taxa de erro (7d) ───────────────────────────────────────
    high_error_jobs = (await db.execute(
        select(
            IngestJob.id,
            IngestJob.source_system,
            IngestJob.failed_records,
            IngestJob.processed_records,
            IngestJob.created_at,
            IngestJob.file_name,
        ).where(
            IngestJob.tenant_id == tid,
            IngestJob.created_at >= since_7d,
            IngestJob.processed_records > 0,
            (IngestJob.failed_records * 10) > IngestJob.processed_records,  # > 10% erro
        )
        .order_by(IngestJob.created_at.desc())
        .limit(10)
    )).all()

    high_error_job_list = [
        {
            "job_id": str(row[0]),
            "source_system": row[1],
            "failed_records": row[2] or 0,
            "processed_records": row[3] or 0,
            "error_rate": round((row[2] or 0) / max(row[3] or 1, 1), 4),
            "created_at": row[4],
            "file_name": row[5],
        }
        for row in high_error_jobs
    ]

    # ── Resumo executivo ──────────────────────────────────────────────────────
    total_snapshots_7d = len(snaps)
    status = "OK"
    if null_ratio_alerts or high_error_job_list:
        status = "WARN"
    if any(a["severity"] == "HIGH" for a in null_ratio_alerts):
        status = "CRITICAL"

    return {
        "generated_at": now,
        "overall_status": status,
        "feature_quality": {
            "snapshots_evaluated_7d": total_snapshots_7d,
            "features_with_null_alerts": len(null_ratio_alerts),
            "null_alerts": null_ratio_alerts,
            "null_ratio_by_feature": feature_null_summary,
            "drift": drift_summary,
        },
        "ingestion_quality": {
            "errors_by_source_30d": ingest_errors_by_source,
            "high_error_jobs_7d": high_error_job_list,
        },
        "data_freshness": data_freshness,
    }

