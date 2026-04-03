"""routers/reports.py — Compliance reports: monthly summary + PDF download (M5)."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, require_roles, require_role, require_role_any
from database import AsyncSessionLocal, get_db
from models import Alert, Bet, Case, FinancialTransaction, Player, ReportPackage, RuleDefinition, User
from utils import write_audit

router = APIRouter(tags=["reports"])

UTC = timezone.utc
logger = structlog.get_logger(__name__)


# ── Pydantic in/out ───────────────────────────────────────────────────────────

class MonthlyReportIn(BaseModel):
    year: int = Field(..., ge=2020, le=2100)
    month: int = Field(..., ge=1, le=12)


# ── Core aggregation helper ───────────────────────────────────────────────────

async def _build_monthly_report(
    tenant_id: str,
    date_from: datetime,
    date_to: datetime,
    db: AsyncSession,
) -> dict:
    """Aggregate compliance statistics for a given period and tenant."""
    # 1. Alerts by severity
    sev_rows = (await db.execute(
        select(Alert.severity, func.count().label("cnt"))
        .where(
            Alert.tenant_id == tenant_id,
            Alert.created_at >= date_from,
            Alert.created_at <= date_to,
        )
        .group_by(Alert.severity)
    )).all()
    alerts_by_severity: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for row in sev_rows:
        if row.severity in alerts_by_severity:
            alerts_by_severity[row.severity] = row.cnt

    # 2. Cases summary
    case_rows = (await db.execute(
        select(Case.status, func.count().label("cnt"))
        .where(
            Case.tenant_id == tenant_id,
            Case.created_at >= date_from,
            Case.created_at <= date_to,
        )
        .group_by(Case.status)
    )).all()
    cases_summary: dict[str, int] = {}
    for row in case_rows:
        key = row.status.lower() if row.status else "unknown"
        cases_summary[key] = row.cnt
    for expected in ("open", "investigating", "closed", "reported"):
        cases_summary.setdefault(expected, 0)

    # 3. Top 10 rules by alert fires
    rule_rows = (await db.execute(
        select(
            Alert.rule_id,
            RuleDefinition.name.label("rule_name"),
            func.count().label("fires"),
        )
        .join(RuleDefinition, RuleDefinition.id == Alert.rule_id, isouter=True)
        .where(
            Alert.tenant_id == tenant_id,
            Alert.created_at >= date_from,
            Alert.created_at <= date_to,
            Alert.rule_id.isnot(None),
        )
        .group_by(Alert.rule_id, RuleDefinition.name)
        .order_by(desc("fires"))
        .limit(10)
    )).all()
    top_rules_by_fires = [
        {
            "rule_id": str(r.rule_id),
            "rule_name": r.rule_name or "(desconhecido)",
            "fires": r.fires,
        }
        for r in rule_rows
    ]

    # 4. Top 10 players by current risk score
    player_rows = (await db.execute(
        select(Player.id, Player.external_player_id, Player.risk_score)
        .where(Player.tenant_id == tenant_id)
        .order_by(Player.risk_score.desc())
        .limit(10)
    )).all()
    top_players_by_risk = [
        {
            "player_id": str(r.id),
            "external_id": r.external_player_id or "",
            "avg_risk_score": float(r.risk_score or 0),
        }
        for r in player_rows
    ]

    # 5. Total ingested events
    tx_count = (await db.execute(
        select(func.count()).where(
            and_(
                FinancialTransaction.tenant_id == tenant_id,
                FinancialTransaction.created_at >= date_from,
                FinancialTransaction.created_at <= date_to,
            )
        )
    )).scalar_one()
    bet_count = (await db.execute(
        select(func.count()).where(
            and_(
                Bet.tenant_id == tenant_id,
                Bet.created_at >= date_from,
                Bet.created_at <= date_to,
            )
        )
    )).scalar_one()
    # Breakdown by product_type for multi-modality visibility
    pt_rows = (await db.execute(
        select(Bet.product_type, func.count().label("cnt"))
        .where(
            Bet.tenant_id == tenant_id,
            Bet.created_at >= date_from,
            Bet.created_at <= date_to,
        )
        .group_by(Bet.product_type)
    )).all()
    bets_by_product_type = {r.product_type or "SPORTSBOOK": r.cnt for r in pt_rows}
    total_ingested_events = (tx_count or 0) + (bet_count or 0)

    # 6. False positive rate
    label_rows = (await db.execute(
        select(Alert.label, func.count().label("cnt"))
        .where(
            Alert.tenant_id == tenant_id,
            Alert.labeled_at >= date_from,
            Alert.labeled_at <= date_to,
            Alert.label.isnot(None),
        )
        .group_by(Alert.label)
    )).all()
    total_labeled = sum(r.cnt for r in label_rows)
    fp_count = sum(r.cnt for r in label_rows if r.label == "FALSE_POSITIVE")
    false_positive_rate: float | None = (
        round(fp_count / total_labeled, 4) if total_labeled > 0 else None
    )
    unknown_count = sum(r.cnt for r in label_rows if r.label == "UNKNOWN")

    # 7. Communications generated during the period
    total_communications_generated = int((
        await db.execute(
            select(func.count()).where(
                and_(
                    ReportPackage.tenant_id == tenant_id,
                    ReportPackage.created_at >= date_from,
                    ReportPackage.created_at <= date_to,
                )
            )
        )
    ).scalar() or 0)

    # 8. Total SAR communications submitted to COAF
    sar_query = text(
        """
        SELECT count(id)
        FROM report_packages
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND created_at >= :date_from
          AND created_at <= :date_to
          AND COALESCE(decision, payload->>'decision', 'PENDING') IN ('REPORT', 'FILE_SAR')
        """
    ).bindparams(tenant_id=tenant_id, date_from=date_from, date_to=date_to)
    sar_res = await db.execute(sar_query)
    total_sar_reports: int = int(sar_res.scalar() or 0)

    # 9. True positive rate
    tp_count = sum(r.cnt for r in label_rows if r.label == "TRUE_POSITIVE")
    true_positive_rate: float | None = (
        round(tp_count / total_labeled * 100, 1) if total_labeled else None
    )
    total_alerts = int(sum(alerts_by_severity.values()))
    total_cases = int(sum(cases_summary.values()))

    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "alerts_by_severity": alerts_by_severity,
        "total_alerts": total_alerts,
        "cases_summary": cases_summary,
        "total_cases": total_cases,
        "total_cases_opened": int(cases_summary.get("open", 0)),
        "total_cases_closed": int(cases_summary.get("closed", 0)),
        "total_cases_reported": int(cases_summary.get("reported", 0)),
        "top_rules_by_fires": top_rules_by_fires,
        "top_players_by_risk": top_players_by_risk,
        "total_ingested_events": total_ingested_events,
        "bets_by_product_type": bets_by_product_type,
        "total_communications_generated": total_communications_generated,
        "false_positive_rate": false_positive_rate,
        "total_sar_reports": total_sar_reports,
        "true_positive_rate": true_positive_rate,
        "quality_metrics": {
            "labeled_alerts": total_labeled,
            "true_positive_count": tp_count,
            "false_positive_count": fp_count,
            "unknown_count": unknown_count,
            "true_positive_rate": true_positive_rate,
            "false_positive_rate": false_positive_rate,
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def _build_monthly_report_background(tenant_id: str, year: int, month: int) -> None:
    """Background wrapper: creates its own DB session and builds the monthly report."""
    import calendar
    date_from = datetime(year, month, 1, tzinfo=UTC)
    last_day = calendar.monthrange(year, month)[1]
    date_to = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    try:
        async with AsyncSessionLocal() as _db:
            report = await _build_monthly_report(tenant_id, date_from, date_to, _db)
            logger.info(
                "monthly_report_background_completed",
                tenant_id=tenant_id,
                year=year,
                month=month,
                total_alerts=sum(report["alerts_by_severity"].values()),
                total_events=report["total_ingested_events"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "monthly_report_background_failed",
            tenant_id=tenant_id,
            year=year,
            month=month,
            error=str(exc),
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/reports/monthly-summary", status_code=202)
async def generate_monthly_report(
    body: MonthlyReportIn,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
):
    """Trigger async generation of monthly compliance summary report (202 Accepted)."""
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "GENERATE_MONTHLY_SUMMARY",
        "Report",
        f"{body.year:04d}-{body.month:02d}",
        after={"year": body.year, "month": body.month},
    )
    await db.commit()
    background_tasks.add_task(
        _build_monthly_report_background,
        current_user.tenant_id,
        body.year,
        body.month,
    )
    return {"status": "queued", "year": body.year, "month": body.month}


@router.get("/reports/monthly-summary")
async def get_monthly_summary(
    date_from: str = Query(..., description="Data inicial YYYY-MM-DD (inclusivo)"),
    date_to: str = Query(..., description="Data final YYYY-MM-DD (inclusivo)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
):
    """Return the monthly compliance summary synchronously."""
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=UTC
        )
    except ValueError:
        raise HTTPException(400, "date_from e date_to devem estar no formato YYYY-MM-DD")
    if df > dt:
        raise HTTPException(400, "date_from não pode ser posterior a date_to")
    report = await _build_monthly_report(current_user.tenant_id, df, dt, db)
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "EXPORT_MONTHLY_SUMMARY_JSON",
        "Report",
        f"{date_from}:{date_to}",
        after={"date_from": date_from, "date_to": date_to},
    )
    await db.commit()
    return report


@router.get("/reports/monthly-summary/csv")
async def get_monthly_summary_csv(
    date_from: str = Query(..., description="Data inicial YYYY-MM-DD"),
    date_to: str = Query(..., description="Data final YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
):
    """Export the monthly compliance summary as a UTF-8-BOM CSV for Excel."""
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC)
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=UTC
        )
    except ValueError:
        raise HTTPException(400, "date_from e date_to devem estar no formato YYYY-MM-DD")
    if df > dt:
        raise HTTPException(400, "date_from não pode ser posterior a date_to")

    report = await _build_monthly_report(current_user.tenant_id, df, dt, db)
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "EXPORT_MONTHLY_SUMMARY_CSV",
        "Report",
        f"{date_from}:{date_to}",
        after={"date_from": date_from, "date_to": date_to},
    )
    await db.commit()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["secao", "chave", "valor"])
    writer.writerow(["Periodo", "de", report["period"]["from"]])
    writer.writerow(["Periodo", "ate", report["period"]["to"]])
    writer.writerow(["Periodo", "gerado_em", report["generated_at"]])

    for sev, cnt in report["alerts_by_severity"].items():
        writer.writerow(["AlertasPorSeveridade", sev, cnt])

    for status_key, cnt in report["cases_summary"].items():
        writer.writerow(["ResumoDeOcorrencias", status_key, cnt])

    writer.writerow(["Totais", "eventos_ingeridos", report["total_ingested_events"]])
    writer.writerow(["Totais", "alertas_total", report["total_alerts"]])
    writer.writerow(["Totais", "casos_total", report["total_cases"]])
    writer.writerow(["Totais", "casos_abertos", report["total_cases_opened"]])
    writer.writerow(["Totais", "casos_fechados", report["total_cases_closed"]])
    writer.writerow(["Totais", "casos_reportados", report["total_cases_reported"]])
    writer.writerow(["Totais", "comunicacoes_geradas", report["total_communications_generated"]])
    writer.writerow(["Totais", "comunicacoes_coaf", report["total_sar_reports"]])
    writer.writerow([
        "Totais",
        "taxa_falso_positivo",
        report["false_positive_rate"] if report["false_positive_rate"] is not None else "N/D",
    ])
    writer.writerow([
        "Totais",
        "taxa_verdadeiro_positivo",
        report["true_positive_rate"] if report["true_positive_rate"] is not None else "N/D",
    ])

    writer.writerow([])
    writer.writerow(["TopRegras", "rule_id", "rule_name", "disparos"])
    for r in report["top_rules_by_fires"]:
        writer.writerow(["TopRegras", r["rule_id"], r["rule_name"], r["fires"]])

    writer.writerow([])
    writer.writerow(["TopJogadores", "player_id", "external_id", "avg_risk_score"])
    for p in report["top_players_by_risk"]:
        writer.writerow(["TopJogadores", p["player_id"], p["external_id"], p["avg_risk_score"]])

    writer.writerow([])
    writer.writerow(["Qualidade", "metrica", "valor"])
    for key, value in report["quality_metrics"].items():
        writer.writerow(["Qualidade", key, value if value is not None else "N/D"])

    csv_bytes = output.getvalue().encode("utf-8-sig")
    filename = f"monthly_summary_{date_from}_{date_to}.csv"
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
