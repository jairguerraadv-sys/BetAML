"""routers/cases.py — Case management: CRUD, assign, events, evidence, report-package."""
from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response as FastAPIResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import AppRole, decrypt_pii, get_current_user, get_effective_roles, mask_cpf, require_roles, require_role, require_role_any, require_permission
from case_refs import build_case_reference_number
from database import get_db
from models import Alert, Bet, Case, CaseEvent, DeviceEvent, FinancialTransaction, Notification, Player, ReportPackage, ScoringConfig, Tenant, User
from repositories import CaseRepository
from repositories.cases import get_case_repo
from utils import redis_rate_limit, write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["cases"])

# ── Case status transition graph ─────────────────────────────────────────────
# Maps each status to the list of valid next statuses.
# REPORTED is terminal: no outbound transitions allowed.
_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "OPEN":           ["INVESTIGATING", "CLOSED"],
    "INVESTIGATING":  ["PENDING_REVIEW", "CLOSED", "OPEN"],
    "PENDING_REVIEW": ["INVESTIGATING", "CLOSED", "REPORTED"],
    "CLOSED":         ["OPEN"],    # allow re-opening
    "REPORTED":       [],          # terminal — no further transitions
}


async def _ensure_case_reference_number(db: AsyncSession, case_obj: Case) -> str:
    reference_number = getattr(case_obj, "reference_number", None)
    if reference_number:
        return str(reference_number)
    reference_number = build_case_reference_number(case_obj)
    case_obj.reference_number = reference_number
    db.add(case_obj)
    return reference_number


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_report_pdf(payload: dict) -> bytes:
    """Gera PDF COAF compacto via reportlab. Levanta RuntimeError se lib indisponível."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, Spacer, SimpleDocTemplate, Table, TableStyle
        from reportlab.lib import colors
    except ImportError as exc:
        raise RuntimeError(
            "reportlab não está instalado. Adicione 'reportlab' ao requirements.txt e reconstrua o container."
        ) from exc

    import io as _io
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    normal = styles["Normal"]
    small = ParagraphStyle("small", parent=normal, fontSize=8)
    story = []
    story.append(Paragraph("BetAML — Pacote de Investigação COAF", h1))
    story.append(Paragraph(
        f"Relatório: {payload.get('report_id', '')} &nbsp;|&nbsp; "
        f"Gerado: {payload.get('generated_at', '')}", small,
    ))
    story.append(Spacer(1, 0.4*cm))
    decision_color = {"FILE_SAR": "#cc0000", "NO_ACTION": "#006600", "PENDING": "#cc6600"}
    decision = payload.get("decision", "PENDING")
    story.append(Paragraph(
        f"Decisão: <b><font color='{decision_color.get(decision, '#000')}'>{decision}</font></b>",
        normal,
    ))
    if payload.get("analyst_narrative"):
        story.append(Paragraph(f"Narrativa: {payload['analyst_narrative']}", normal))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Sujeito da Operação", h2))
    subject = payload.get("subject", {})
    subj_data = [["Campo", "Valor"]] + [[k, str(v)] for k, v in subject.items() if v is not None]
    if len(subj_data) > 1:
        t = Table(subj_data, colWidths=[5*cm, 12*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Operações Suspeitas", h2))
    ops = payload.get("suspicious_operations", [])
    if ops:
        rows = [["Alerta ID", "Título", "Severidade", "Tipo", "Data"]]
        for op in ops:
            rows.append([
                str(op.get("alert_id", ""))[:8],
                str(op.get("title", ""))[:40],
                str(op.get("severity", "")),
                str(op.get("alert_type", "")),
                str(op.get("occurred_at", ""))[:19],
            ])
        t = Table(rows, colWidths=[2*cm, 6*cm, 2.5*cm, 2.5*cm, 4*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ]))
        story.append(t)

    # ── Siscoaf 97 — Tabela de Ocorrências (Portaria SPA/MF 1.143/2024) ──────
    siscoaf = payload.get("siscoaf") or {}
    occ_codes = siscoaf.get("occurrence_codes") or []
    inv_types = siscoaf.get("involvement_types") or []
    valor_premio = siscoaf.get("valor_premio", 0.0)
    valor_apostas = siscoaf.get("valor_apostas", 0.0)
    info_adicionais = siscoaf.get("informacoes_adicionais", "")
    portaria = siscoaf.get("portaria_referencia", "SPA/MF 1.143/2024")

    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Siscoaf — Portaria SPA/MF 1.143/2024 (Comunicado 97)", h2))
    story.append(Paragraph(
        f"Portaria: <b>{portaria}</b> &nbsp;|&nbsp; Comunicado Siscoaf: <b>97</b>", small,
    ))
    story.append(Spacer(1, 0.2*cm))

    siscoaf_meta = [
        ["Campo", "Valor"],
        ["Valor do Prêmio (R$)", f"{float(valor_premio):.2f}"],
        ["Valor das Apostas (R$)", f"{float(valor_apostas):.2f}"],
        ["Informações Adicionais", str(info_adicionais)[:200] if info_adicionais else "—"],
    ]
    ts = Table(siscoaf_meta, colWidths=[5.5*cm, 11.5*cm])
    ts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7f1d1d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#fff1f2"), colors.white]),
    ]))
    story.append(ts)
    story.append(Spacer(1, 0.2*cm))

    if occ_codes:
        story.append(Paragraph("Códigos de Ocorrência Siscoaf", ParagraphStyle("h3", parent=normal, fontSize=9, fontName="Helvetica-Bold")))
        occ_rows = [["Código", "Descrição"]]
        occ_descs = siscoaf.get("occurrence_descriptions") or {}
        for code in occ_codes:
            occ_rows.append([str(code), str(occ_descs.get(str(code), "—"))[:80]])
        to = Table(occ_rows, colWidths=[2*cm, 15*cm])
        to.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#991b1b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#fff1f2"), colors.white]),
        ]))
        story.append(to)
        story.append(Spacer(1, 0.2*cm))

    if inv_types:
        story.append(Paragraph("Tipos de Envolvimento", ParagraphStyle("h3", parent=normal, fontSize=9, fontName="Helvetica-Bold")))
        inv_rows = [["Código", "Descrição"]]
        inv_descs = siscoaf.get("involvement_descriptions") or {}
        for tipo in inv_types:
            inv_rows.append([str(tipo), str(inv_descs.get(str(tipo), "—"))])
        ti = Table(inv_rows, colWidths=[2*cm, 15*cm])
        ti.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7c3aed")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f5f3ff"), colors.white]),
        ]))
        story.append(ti)

    doc.build(story)
    return buf.getvalue()


# ── COAF Siscoaf 97 — Tabelas de Ocorrência e Envolvimento (Portaria SPA/MF 1.143/2024) ─────
# Codes from Comunicado Siscoaf 97 (30/12/2024) — vigência 01/04/2025
SISCOAF_OCCURRENCE_CODES: dict[int, str] = {
    1407: "Art. 24-I — Falta de fundamento econômico ou legal",
    1408: "Art. 24-II — Incompatibilidade com práticas usuais de mercado",
    1409: "Art. 24-III — Possível indício de lavagem de dinheiro ou financiamento ao terrorismo",
    1410: "Art. 25-I — Pessoa envolvida em LD ou crimes financeiros",
    1411: "Art. 25-II — Terrorismo / proliferação de armas",
    1412: "Art. 25-III — Jurisdição GAFI de alto risco ou sob monitoramento",
    1413: "Art. 25-IV — Resistência a fornecer informações cadastrais",
    1414: "Art. 25-V — Informações falsas ou de difícil verificação",
    1415: "Art. 25-VI — Aporte suspeito quanto à origem dos recursos",
    1416: "Art. 25-VII — Prêmio suspeito de ser instrumento de LD/FTP/fraude",
    1417: "Art. 25-VIII — Manipulação de resultados",
    1418: "Art. 25-IX — Incompatibilidade comportamental com o perfil",
    1419: "Art. 25-X — Utilização de ferramenta automatizada (bots)",
    1420: "Art. 25-XI — Fracionamento / dissimulação de operações",
    1421: "Art. 25-XII — Retirada imediata pós-depósito sem apostas",
    1422: "Art. 25-XIII — Utilização indevida de conta de terceiro",
    1423: "Art. 25-XIV — Agente intermediador de apostas",
    1424: "Art. 25-XV — Aportes sugestivos de intermediação de apostas",
    1425: "Art. 25-XVI — Uso de plataforma bet exchange para LD/FTP",
    1426: "Art. 25-XVII — Pessoa Politicamente Exposta (PEP)",
    1427: "Art. 25-XVIII — Dificuldade de realização cadastral",
    1428: "Art. 25-XIX — Qualquer operação com características atípicas (catch-all)",
}

SISCOAF_INVOLVEMENT_TYPES: dict[int, str] = {
    1:  "Titular",
    8:  "Outros",
    49: "Apostador",
    50: "Usuário de Plataforma",
}

_VALID_OCCURRENCE_CODES = set(SISCOAF_OCCURRENCE_CODES.keys())
_VALID_INVOLVEMENT_TYPES = set(SISCOAF_INVOLVEMENT_TYPES.keys())


# ── Schemas ───────────────────────────────────────────────────────────────────

class CaseCreate(BaseModel):
    player_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    severity: str = "HIGH"


class AssignRequest(BaseModel):
    user_id: str


class ReportPackageIn(BaseModel):
    analyst_narrative: Optional[str] = None
    decision: Optional[str] = Field(default="PENDING", pattern="^(FILE_SAR|NO_ACTION|PENDING)$")
    # ── Siscoaf Comunicado 97 — campos obrigatórios (Portaria SPA/MF 1.143/2024) ──
    occurrence_codes: list[int] = Field(
        default_factory=list,
        description="Códigos de ocorrência Siscoaf (1407–1428). Obrigatório para decision=FILE_SAR.",
    )
    involvement_types: list[int] = Field(
        default_factory=lambda: [49],
        description="Tipos de envolvimento Siscoaf: 1=Titular, 8=Outros, 49=Apostador, 50=Usuário de Plataforma.",
    )
    valor_premio: float = Field(
        default=0.0, ge=0,
        description="Valor do prêmio recebido pelo apostador (R$). ≥ 0.",
    )
    valor_apostas: float = Field(
        default=0.0, ge=0,
        description="Valor total das apostas no período analisado (R$). ≥ 0.",
    )
    informacoes_adicionais: Optional[str] = Field(
        default=None,
        description="Informações adicionais obrigatórias para todos os códigos de ocorrência.",
    )


class CaseEventCreate(BaseModel):
    event_type: str = "NOTE"
    content: dict[str, Any]


class CaseCommentIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    mentions: list[str] = Field(default_factory=list)


class CaseLinkAlertIn(BaseModel):
    alert_id: str


class CaseLinkTransactionIn(BaseModel):
    transaction_id: str


def _map_report_decision(decision: str) -> str:
    mapping = {
        "FILE_SAR": "REPORT",
        "NO_ACTION": "CLOSE",
        "PENDING": "MONITOR",
    }
    return mapping.get(decision, "MONITOR")


async def _resolve_generated_by(db: AsyncSession, user_id: str | None) -> str:
    if not user_id:
        return "system"
    user = await db.get(User, user_id)
    if not user:
        return str(user_id)
    return f"{user.username} ({user.role})"


async def _build_report_payload(
    *,
    db: AsyncSession,
    case_obj: Case,
    alerts: list[Alert],
    events: list[CaseEvent],
    current_user: User,
    analyst_narrative: str | None,
    decision_code: str,
    occurrence_codes: list[int] | None = None,
    involvement_types: list[int] | None = None,
    valor_premio: float = 0.0,
    valor_apostas: float = 0.0,
    informacoes_adicionais: str | None = None,
) -> dict[str, Any]:
    player = await db.get(Player, case_obj.player_id) if case_obj.player_id else None
    tenant = await db.get(Tenant, case_obj.tenant_id)
    generated_at = datetime.now(UTC)
    generated_by = await _resolve_generated_by(db, str(current_user.id))
    report_id = str(uuid.uuid4())

    subject: dict[str, Any] = {
        "cpf": None,
        "name": None,
        "birthDate": None,
        "pepFlag": False,
        "riskCategory": None,
        "profession": None,
        "declaredIncomeMonthly": 0,
        "registeredSince": None,
    }
    if player:
        cpf_plain = decrypt_pii(player.cpf_encrypted)  # type: ignore[arg-type]
        name_plain = decrypt_pii(player.name_encrypted)  # type: ignore[arg-type]
        subject = {
            "cpf": mask_cpf(cpf_plain),
            "name": name_plain,
            "birthDate": player.birth_date.isoformat() if player.birth_date else None,
            "pepFlag": bool(player.pep_flag),
            "riskCategory": str(player.risk_band or "LOW"),
            "profession": player.profession,
            "declaredIncomeMonthly": float(player.declared_income_monthly or 0),
            "registeredSince": player.registered_since.isoformat() if player.registered_since else None,
        }

    txns = []
    bets = []
    primary_instruments: list[str] = []
    unusual_patterns: list[str] = []
    if case_obj.player_id:
        txns = list((await db.execute(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.tenant_id == case_obj.tenant_id,
                FinancialTransaction.player_id == case_obj.player_id,
                FinancialTransaction.occurred_at >= generated_at - timedelta(days=90),
            )
            .order_by(FinancialTransaction.occurred_at.desc())
            .limit(50)
        )).scalars().all())
        bets = list((await db.execute(
            select(Bet)
            .where(
                Bet.tenant_id == case_obj.tenant_id,
                Bet.player_id == case_obj.player_id,
                Bet.occurred_at >= generated_at - timedelta(days=90),
            )
            .order_by(Bet.occurred_at.desc())
            .limit(50)
        )).scalars().all())
        primary_instruments = list({
            str(tx.payment_instrument)
            for tx in txns
            if getattr(tx, "payment_instrument", None)
        })[:10]

    top_drivers = [
        str(driver)
        for alert in alerts
        if isinstance(alert.evidence, dict)
        for driver in (alert.evidence.get("top_drivers") or [])
    ]
    if top_drivers:
        unusual_patterns.append("high_ml_driver_overlap")
    if any(str(alert.severity) == "CRITICAL" for alert in alerts):
        unusual_patterns.append("critical_alert_present")
    if player and player.pep_flag:
        unusual_patterns.append("pep_player")

    total_deposits_90d = sum(float(tx.amount or 0) for tx in txns if str(getattr(tx, "type", "")) == "DEPOSIT")
    total_withdrawals_90d = sum(float(tx.amount or 0) for tx in txns if str(getattr(tx, "type", "")) == "WITHDRAWAL")
    total_bet_stake_90d = sum(float(bet.stake_amount or 0) for bet in bets)

    alerts_summary = []
    for alert in alerts:
        evidence = alert.evidence if isinstance(alert.evidence, dict) else {}
        alerts_summary.append({
            "alertId": str(alert.id),
            "type": str(alert.alert_type or "RULE"),
            "severity": str(alert.severity or "LOW"),
            "ruleOrModel": (
                str(evidence.get("model_id"))
                if evidence.get("model_id")
                else str(alert.rule_id or alert.compound_rule_id or alert.alert_type)
            ),
            "description": str(alert.description or alert.title or ""),
            "evidence": evidence,
        })

    attachments = [
        {
            "eventId": str(event.id),
            "fileName": event.content.get("file_name"),
            "description": event.content.get("description"),
        }
        for event in events
        if event.event_type == "EVIDENCE_UPLOAD" and isinstance(event.content, dict)
    ]

    final_payload = {
        "reportId": report_id,
        "tenantId": str(case_obj.tenant_id),
        "caseNumber": str(case_obj.reference_number or build_case_reference_number(case_obj)),
        "generatedAt": generated_at.isoformat(),
        "generatedBy": generated_by,
        "subject": subject,
        "financialSummary": {
            "totalDeposits90d": round(total_deposits_90d, 2),
            "totalWithdrawals90d": round(total_withdrawals_90d, 2),
            "totalBetStake90d": round(total_bet_stake_90d, 2),
            "primaryInstruments": primary_instruments,
            "unusualPatterns": unusual_patterns,
        },
        "alertsSummary": alerts_summary,
        "keyTransactions": [
            {
                "transactionId": str(tx.id),
                "type": str(tx.type),
                "amount": float(tx.amount or 0),
                "status": str(tx.status),
                "occurredAt": tx.occurred_at.isoformat() if tx.occurred_at else None,
                "paymentInstrument": getattr(tx, "payment_instrument", None),
            }
            for tx in txns[:20]
        ],
        "keyBets": [
            {
                "betId": str(bet.id),
                "stakeAmount": float(bet.stake_amount or 0),
                "actualPayout": float(bet.actual_payout or 0) if getattr(bet, "actual_payout", None) is not None else None,
                "status": str(getattr(bet, "status", "")),
                "occurredAt": bet.occurred_at.isoformat() if bet.occurred_at else None,
            }
            for bet in bets[:20]
        ],
        "analystNarrative": analyst_narrative or "",
        "decision": _map_report_decision(decision_code),
        "decisionLegacy": decision_code,
        "attachments": attachments,
        # ── Siscoaf Comunicado 97 — campos obrigatórios (Portaria SPA/MF 1.143/2024) ──
        "siscoaf": {
            "occurrence_codes": [c for c in (occurrence_codes or []) if c in _VALID_OCCURRENCE_CODES],
            "occurrence_descriptions": {
                str(c): SISCOAF_OCCURRENCE_CODES[c]
                for c in (occurrence_codes or [])
                if c in _VALID_OCCURRENCE_CODES
            },
            "involvement_types": [t for t in (involvement_types or [49]) if t in _VALID_INVOLVEMENT_TYPES],
            "involvement_descriptions": {
                str(t): SISCOAF_INVOLVEMENT_TYPES[t]
                for t in (involvement_types or [49])
                if t in _VALID_INVOLVEMENT_TYPES
            },
            "valor_premio": round(valor_premio, 2),
            "valor_apostas": round(valor_apostas, 2),
            "informacoes_adicionais": informacoes_adicionais or "",
            "portaria_referencia": "SPA/MF 1.143/2024",
            "comunicado_siscoaf": "97",
        },
        # backward-compatible fields kept for existing consumers/tests
        "report_id": report_id,
        "schema_version": "2.0",
        "generated_at": generated_at.isoformat(),
        "generated_by": str(current_user.id),
        "reporting_entity": {
            "tenant_id": str(case_obj.tenant_id),
            "tenant_name": getattr(tenant, "name", None),
            "platform": "BetAML",
        },
        "case": {
            "id": str(case_obj.id),
            "title": case_obj.title,
            "status": case_obj.status,
            "severity": case_obj.severity,
            "opened_at": case_obj.created_at.isoformat() if case_obj.created_at else None,
        },
        "suspicious_operations": [
            {
                "alert_id": item["alertId"],
                "title": alert.title,
                "severity": item["severity"],
                "alert_type": item["type"],
                "evidence": item["evidence"],
                "occurred_at": alert.created_at.isoformat() if alert.created_at else None,
            }
            for item, alert in zip(alerts_summary, alerts)
        ],
        "financial_summary": {
            "total_alerts": len(alerts),
            "total_amount_brl": round(total_deposits_90d + total_withdrawals_90d, 2),
            "max_single_amount_brl": round(max([float(tx.amount or 0) for tx in txns] + [0.0]), 2),
            "alert_types": list({str(alert.alert_type) for alert in alerts}),
        },
        "investigation_timeline": [
            {
                "event_type": event.event_type,
                "content": event.content,
                "recorded_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ],
        "analyst_narrative": analyst_narrative or "",
        "decision_basis": "Análise conforme COAF Res. 36/2021 e regulamentação Bacen/MF",
    }
    return final_payload

def _suggest_analyst_narrative(case_obj: Case, alerts: list[Alert], player_info: dict) -> str:
    """Gera narrativa base para o analista revisar antes da decisão final."""
    severity_set = sorted({str(a.severity or "UNKNOWN") for a in alerts})
    alert_types = sorted({str(a.alert_type or "RULE") for a in alerts})
    total_alerts = len(alerts)
    total_amount = 0.0
    for a in alerts:
        if isinstance(a.evidence, dict):
            try:
                total_amount += float(a.evidence.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue

    subject = player_info.get("external_player_id") or str(case_obj.player_id or "não identificado")
    pep = "SIM" if player_info.get("pep_flag") else "NÃO"

    return (
        f"No período analisado, foram identificados {total_alerts} alerta(s) para o jogador {subject}, "
        f"com severidades observadas: {', '.join(severity_set) if severity_set else 'N/A'} e "
        f"tipologias: {', '.join(alert_types) if alert_types else 'N/A'}. "
        f"A soma aproximada dos valores associados aos alertas é de R$ {total_amount:,.2f}. "
        f"Indicativo PEP: {pep}. "
        "A recomendação preliminar é aprofundar diligências sobre origem/destino de recursos, "
        "consistência econômico-financeira e eventual padrão de structuring/round-tripping, "
        "com decisão final condicionada à validação documental complementar."
    )

# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/cases", status_code=201)
async def create_case(
    body: CaseCreate,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    resolved_player_id: Optional[str] = None
    if body.player_id:
        try:
            import uuid

            uuid.UUID(str(body.player_id))
            resolved_player_id = body.player_id
        except Exception:
            # Accept external_player_id values (tests use 'test')
            try:
                from models import Player

                resolved_player_id = (
                    await db.execute(
                        select(Player.id).where(
                            Player.tenant_id == current_user.tenant_id,
                            Player.external_player_id == body.player_id,
                        ).limit(1)
                    )
                ).scalar_one_or_none()
                if resolved_player_id is not None:
                    resolved_player_id = str(resolved_player_id)
            except Exception:
                resolved_player_id = None

    c = Case(
        tenant_id=current_user.tenant_id,
        player_id=resolved_player_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        created_by=current_user.id,
    )
    db.add(c)
    await db.flush()
    # Auto-set SLA deadline from tenant ScoringConfig (or fallback defaults)
    sc = (await db.execute(
        select(ScoringConfig).where(ScoringConfig.tenant_id == current_user.tenant_id).limit(1)
    )).scalar_one_or_none()
    _sla_hours = {
        "CRITICAL": int(sc.sla_critical_hours) if sc else 4,
        "HIGH":     int(sc.sla_high_hours)     if sc else 24,
        "MEDIUM":   int(sc.sla_medium_hours)   if sc else 72,
        "LOW":      int(sc.sla_low_hours)       if sc else 168,
    }
    c.sla_due_at = datetime.now(UTC) + timedelta(hours=_sla_hours.get(body.severity, 24))
    reference_number = await _ensure_case_reference_number(db, c)
    await write_audit(db, current_user.tenant_id, current_user.id, "CREATE", "Case", c.id, after=body.model_dump())
    response_payload = {"id": c.id, "title": c.title, "status": c.status, "reference_number": reference_number}
    await db.commit()
    try:
        await db.refresh(c)
    except Exception:
        pass
    return response_payload


@router.get("/cases")
async def list_cases(
    status_filter: Optional[str] = None,
    player_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    repo: CaseRepository = Depends(get_case_repo),
):
    cases = await repo.list_filtered(
        current_user.tenant_id,
        status=status_filter,
        player_id=player_id,
        limit=limit,
        offset=offset,
    )
    repo_db = getattr(repo, "db", None)
    missing_references = False
    if repo_db is not None:
        for case_obj in cases:
            if not getattr(case_obj, "reference_number", None):
                await _ensure_case_reference_number(repo_db, case_obj)
                missing_references = True
        if missing_references:
            await repo_db.commit()
    return [
        {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "player_id": c.player_id, "assigned_to": c.assigned_to, "created_at": c.created_at,
            "reference_number": getattr(c, "reference_number", None) or build_case_reference_number(c),
            "priority": getattr(c, "priority", "MEDIUM"),
            "sla_due_at": getattr(c, "sla_due_at", None),
            "auto_created": getattr(c, "auto_created", False),
        }
        for c in cases
    ]


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    current_user: User = Depends(get_current_user),
    repo: CaseRepository = Depends(get_case_repo),
    db: AsyncSession = Depends(get_db),
):
    c = await repo.get_by_id(current_user.tenant_id, case_id)
    if not c:
        raise HTTPException(404, "Caso não encontrado")
    if not getattr(c, "reference_number", None):
        await _ensure_case_reference_number(db, c)
        await db.commit()
    alerts = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events = (await db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at)
    )).scalars().all()
    report_packages = (await db.execute(
        select(ReportPackage)
        .where(
            ReportPackage.case_id == case_id,
            ReportPackage.tenant_id == current_user.tenant_id,
        )
        .order_by(ReportPackage.created_at.desc())
    )).scalars().all()
    return {
        "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
        "description": c.description, "player_id": c.player_id,
        "assigned_to": c.assigned_to, "created_at": c.created_at,
        "reference_number": getattr(c, "reference_number", None) or build_case_reference_number(c),
        "priority": getattr(c, "priority", "MEDIUM"),
        "sla_due_at": getattr(c, "sla_due_at", None),
        "auto_created": getattr(c, "auto_created", False),
        "alerts": [{"id": a.id, "severity": a.severity, "title": a.title} for a in alerts],
        "timeline": [
            {"id": e.id, "event_type": e.event_type, "content": e.content, "created_at": e.created_at}
            for e in events
        ],
        "report_packages": [
            {
                "id": rp.id,
                "status": rp.status,
                "format": rp.format,
                "decision": ((rp.payload or {}).get("decisionLegacy") or (rp.payload or {}).get("decision") or rp.decision) if isinstance(rp.payload, dict) else rp.decision,
                "created_at": rp.created_at,
                "generated_by": rp.created_by,
                "pdf_available": rp.pdf_path is not None,
            }
            for rp in report_packages
        ],
    }


@router.post("/cases/{case_id}/assign")
async def assign_case(
    case_id: str,
    body: AssignRequest,
    current_user: User = Depends(require_role(AppRole.GESTOR)),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    c.assigned_to = body.user_id  # type: ignore[assignment]
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="ASSIGNMENT", content={"assigned_to": body.user_id},
        created_by=current_user.id,
    )
    db.add(evt)
    db.add(Notification(
        tenant_id=current_user.tenant_id,
        user_id=body.user_id,
        type="CASE_ASSIGNED",
        title=f"Caso atribuído: {c.title}",
        body=f"O caso foi atribuído a você por {current_user.username}.",
        reference_type="Case",
        reference_id=case_id,
    ))
    await write_audit(db, current_user.tenant_id, current_user.id, "ASSIGN", "Case", case_id, after={"assigned_to": body.user_id})
    await db.commit()
    return {"case_id": case_id, "assigned_to": body.user_id}


@router.post("/cases/{case_id}/events", status_code=201)
async def add_case_event(
    case_id: str,
    body: CaseEventCreate,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    if body.event_type == "STATUS_CHANGE":
        new_status = body.content.get("new_status")
        if new_status:
            allowed = _STATUS_TRANSITIONS.get(str(c.status), [])
            if new_status not in allowed:
                raise HTTPException(
                    400,
                    f"Transição de status inválida: '{c.status}' → '{new_status}'. "
                    f"Transições permitidas de '{c.status}': "
                    f"{allowed if allowed else ['nenhuma (status terminal)']}",
                )
            c.status = new_status  # type: ignore[assignment]
            if new_status in ("CLOSED", "REPORTED"):
                c.closed_by = current_user.id  # type: ignore[assignment]
                c.closed_at = datetime.now(UTC)  # type: ignore[assignment]
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type=body.event_type, content=body.content, created_by=current_user.id,
    )
    db.add(evt)
    await write_audit(db, current_user.tenant_id, current_user.id, f"CASE_{body.event_type}", "Case", case_id, after=body.content)
    await db.commit()
    await db.refresh(evt)
    return {"id": evt.id, "event_type": evt.event_type, "created_at": evt.created_at}


@router.post("/cases/{case_id}/evidence")
async def upload_evidence(
    case_id: str,
    file: UploadFile = File(...),
    description: str = Form(""),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="EVIDENCE_UPLOAD",
        content={"file_name": file.filename, "description": description, "size": 0},
        created_by=current_user.id,
    )
    db.add(evt)
    await db.commit()
    return {"case_id": case_id, "file_name": file.filename, "status": "uploaded"}


@router.post("/cases/{case_id}/report-package", status_code=201)
async def generate_report_package(
    case_id: str,
    body: ReportPackageIn = ReportPackageIn(),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    await _ensure_case_reference_number(db, c)
    decision = body.decision or "PENDING"
    persisted_decision = _map_report_decision(decision)
    if decision == "FILE_SAR" and not body.analyst_narrative:
        raise HTTPException(400, "analyst_narrative é obrigatório quando decision=FILE_SAR (COAF Res. 36/2021 Art. 9)")

    # ── Siscoaf 97: validações obrigatórias para comunicação COAF ─────────────
    if decision == "FILE_SAR":
        invalid_codes = [c for c in body.occurrence_codes if c not in _VALID_OCCURRENCE_CODES]
        if invalid_codes:
            raise HTTPException(
                400,
                f"Códigos de ocorrência inválidos: {invalid_codes}. "
                f"Valores aceitos: {sorted(_VALID_OCCURRENCE_CODES)}",
            )
        if not body.occurrence_codes:
            raise HTTPException(
                400,
                "occurrence_codes é obrigatório para decision=FILE_SAR "
                "(Portaria SPA/MF 1.143/2024, Comunicado Siscoaf 97)",
            )
        invalid_types = [t for t in body.involvement_types if t not in _VALID_INVOLVEMENT_TYPES]
        if invalid_types:
            raise HTTPException(
                400,
                f"Tipos de envolvimento inválidos: {invalid_types}. "
                f"Valores aceitos: {sorted(_VALID_INVOLVEMENT_TYPES)}",
            )
        if not body.informacoes_adicionais or not body.informacoes_adicionais.strip():
            raise HTTPException(
                400,
                "informacoes_adicionais é obrigatório para todos os códigos de ocorrência Siscoaf "
                "(Comunicado 97 — campo não pode ser nulo)",
            )

    alerts  = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events  = (await db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id))).scalars().all()

    payload = await _build_report_payload(
        db=db,
        case_obj=c,
        alerts=list(alerts),
        events=list(events),
        current_user=current_user,
        analyst_narrative=body.analyst_narrative,
        decision_code=decision,
        occurrence_codes=body.occurrence_codes,
        involvement_types=body.involvement_types,
        valor_premio=body.valor_premio,
        valor_apostas=body.valor_apostas,
        informacoes_adicionais=body.informacoes_adicionais,
    )

    rp = ReportPackage(
        tenant_id=current_user.tenant_id, case_id=case_id, player_id=c.player_id,
        payload=payload, analyst_narrative=body.analyst_narrative,
        decision=persisted_decision,
        status="DRAFT" if decision == "PENDING" else "FINAL",
        created_by=current_user.id,
    )
    db.add(rp)

    pdf_path: str | None = None
    try:
        pdf_bytes = await asyncio.get_event_loop().run_in_executor(None, _build_report_pdf, payload)
        if pdf_bytes:
            import os as _os
            import tempfile as _tmp
            pdf_filename = f"reports/{current_user.tenant_id}/{payload['reportId']}.pdf"
            try:
                from minio import Minio as _Minio
                minio_url = _os.getenv("MINIO_URL", "minio:9000")
                mc = _Minio(minio_url, access_key=_os.getenv("MINIO_ACCESS_KEY", "minio"),
                            secret_key=_os.getenv("MINIO_SECRET_KEY", "minio123"), secure=False)
                bucket = "betaml-reports"
                if not mc.bucket_exists(bucket):
                    mc.make_bucket(bucket)
                import io as _io
                mc.put_object(bucket, pdf_filename, _io.BytesIO(pdf_bytes),
                              len(pdf_bytes), content_type="application/pdf")
                pdf_path = f"minio://{bucket}/{pdf_filename}"
            except Exception:
                tmp_path = _os.path.join(_tmp.gettempdir(), f"{payload['reportId']}.pdf")
                with open(tmp_path, "wb") as _f:
                    _f.write(pdf_bytes)
                pdf_path = tmp_path
            rp.pdf_path = pdf_path  # type: ignore[assignment]
    except RuntimeError as pdf_dep_exc:
        logger.error("pdf_dependency_missing", error=str(pdf_dep_exc))
        raise HTTPException(503, "Geração de PDF indisponível — reportlab não instalado no servidor") from pdf_dep_exc
    except Exception as pdf_exc:
        logger.warning("pdf_generation_failed", error=str(pdf_exc))

    db.add(CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="REPORT_GENERATED",
        content={"report_id": payload["reportId"], "decision": decision},
        created_by=current_user.id,
    ))
    await write_audit(db, current_user.tenant_id, current_user.id, "GENERATE_REPORT", "Case", case_id,
                      after={"report_id": payload["reportId"], "decision": decision})
    await db.commit()
    return {
        "report_package_id": rp.id, "status": rp.status,
        "decision": decision,
        "pdf_path": rp.pdf_path, "payload": payload,
    }


@router.get("/cases/{case_id}/report-package/narrative-suggest")
async def suggest_report_narrative(
    case_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Sugere narrativa inicial para o analista revisar no ReportPackage."""
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")

    alerts = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()

    player_info: dict = {}
    if c.player_id is not None:
        p = await db.get(Player, c.player_id)
        if p:
            player_info = {
                "player_id": p.id,
                "external_player_id": p.external_player_id,
                "pep_flag": p.pep_flag,
                "risk_score": float(p.risk_score),  # type: ignore[arg-type]
            }

    narrative = _suggest_analyst_narrative(c, alerts, player_info)
    return {
        "case_id": case_id,
        "suggested_narrative": narrative,
        "alerts_considered": len(alerts),
        "player": player_info,
    }


@router.post("/cases/{case_id}/report-package/submit")
async def submit_report_package(
    case_id: str,
    current_user: User = Depends(require_role(AppRole.GESTOR)),
    db: AsyncSession = Depends(get_db),
):
    """
    Submete o ReportPackage mais recente ao COAF (stub para integração futura).

    Comportamento atual:
    - Valida que existe um ReportPackage com decision=FILE_SAR para o caso
    - Marca o report como FILED (status de comunicação confirmada)
    - Registra no audit_log: SUBMIT_COAF_REPORT
    - Persiste CaseEvent com tipo REPORT_SUBMITTED

    Integração futura:
    - Quando o portal COAF disponibilizar API REST, este endpoint
      fará HTTP POST do payload para o endpoint oficial.

    Returns:
        JSON com status da submissão e identificador de rastreamento.
    """
    await redis_rate_limit(str(current_user.tenant_id), "cases.report.submit", max_requests=10)

    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")

    # Buscar o ReportPackage mais recente para este caso
    from sqlalchemy import select as _select
    from models import ReportPackage as _RP

    rp = (await db.execute(
        _select(_RP).where(
            _RP.case_id == case_id,
            _RP.tenant_id == current_user.tenant_id,
        ).order_by(_RP.created_at.desc())
    )).scalar_one_or_none()

    if rp is None:
        raise HTTPException(
            400,
            "Nenhum ReportPackage encontrado para este caso. "
            "Gere primeiro com POST /cases/{id}/report-package."
        )

    # Maker-checker: quem gera o ReportPackage não pode submetê-lo.
    if rp.created_by and str(rp.created_by) == str(current_user.id):
        raise HTTPException(
            403,
            "Maker-checker: o usuário que gerou o ReportPackage não pode submetê-lo. "
            "A submissão deve ser feita por outro usuário com perfil ADMIN."
        )

    payload_decision = (rp.payload or {}).get("decisionLegacy") if isinstance(rp.payload, dict) else None
    if payload_decision is None and isinstance(rp.payload, dict):
        raw_decision = str((rp.payload or {}).get("decision", "MONITOR"))
        reverse = {"REPORT": "FILE_SAR", "CLOSE": "NO_ACTION", "MONITOR": "PENDING"}
        # Retrocompatibilidade:
        # - payloads novos persistem REPORT/CLOSE/MONITOR
        # - payloads legados ainda podem carregar FILE_SAR/NO_ACTION/PENDING
        if raw_decision in {"FILE_SAR", "NO_ACTION", "PENDING"}:
            payload_decision = raw_decision
        else:
            payload_decision = reverse.get(raw_decision, "PENDING")
    if payload_decision != "FILE_SAR":
        raise HTTPException(
            400,
            f"ReportPackage deve ter decision=FILE_SAR para ser submetido. "
            f"Decision atual: '{payload_decision}'."
        )

    if str(rp.status) == "FILED":
        raise HTTPException(409, "Este ReportPackage já foi submetido anteriormente.")

    # Marcar como FILED
    rp.status = "FILED"  # type: ignore[assignment]
    if str(c.status) != "REPORTED":
        c.status = "REPORTED"  # type: ignore[assignment]
    if not c.closed_at:
        c.closed_at = datetime.now(UTC)  # type: ignore[assignment]
    if not c.closed_by:
        c.closed_by = current_user.id  # type: ignore[assignment]

    # Registrar evento no caso
    import uuid as _uuid
    tracking_id = str(_uuid.uuid4())
    db.add(CaseEvent(
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        event_type="REPORT_SUBMITTED",
        content={
            "report_package_id": rp.id,
            "tracking_id": tracking_id,
            "case_status": c.status,
            "submitted_by": current_user.id,
            "submitted_at": datetime.now(UTC).isoformat(),
            "channel": "STUB_MANUAL",
            "note": (
                "Submissão registrada. Quando o portal COAF disponibilizar API, "
                "este endpoint será atualizado para envio automático."
            ),
        },
        created_by=current_user.id,
    ))

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "SUBMIT_COAF_REPORT", "Case", case_id,
        after={"report_package_id": rp.id, "tracking_id": tracking_id, "case_status": c.status},
    )
    await db.commit()

    logger.info(
        "coaf_report_submitted",
        case_id=case_id,
        report_package_id=rp.id,
        tracking_id=tracking_id,
        user_id=current_user.id,
    )

    return {
        "status": "FILED",
        "report_package_id": rp.id,
        "tracking_id": tracking_id,
        "submitted_at": datetime.now(UTC).isoformat(),
        "submitted_by": current_user.id,
        "channel": "STUB_MANUAL",
        "message": (
            "Submissão registrada com sucesso. "
            "Guarde o tracking_id para rastreamento. "
            "Quando a API COAF estiver disponível, o envio será automático."
        ),
    }


@router.get("/cases/{case_id}/report-packages")
async def list_report_packages(
    case_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Lista todos os ReportPackages para um caso, ordenados por data de criação decrescente.

    Cada item inclui: id, status, format, decision, created_at, generated_by (user_id)
    e se o PDF está disponível (bool).

    Returns:
        Lista de dicionários com metadados de cada ReportPackage.
    """
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")

    rps = (await db.execute(
        select(ReportPackage)
        .where(
            ReportPackage.case_id == case_id,
            ReportPackage.tenant_id == current_user.tenant_id,
        )
        .order_by(ReportPackage.created_at.desc())
    )).scalars().all()

    return [
        {
            "id": rp.id,
            "status": rp.status,
            "format": rp.format,
            "decision": (
                (rp.payload or {}).get("decision", rp.decision)
                if rp.payload is not None
                else rp.decision
            ),
            "created_at": rp.created_at,
            "generated_by": rp.created_by,
            "pdf_available": rp.pdf_path is not None,
        }
        for rp in rps
    ]


@router.get("/report-packages")
async def list_tenant_report_packages(
    case_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ReportPackage)
        .where(ReportPackage.tenant_id == current_user.tenant_id)
        .order_by(ReportPackage.created_at.desc())
        .limit(limit)
    )
    if case_id:
        stmt = stmt.where(ReportPackage.case_id == case_id)
    if status:
        stmt = stmt.where(ReportPackage.status == status)
    rps = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": rp.id,
            "case_id": rp.case_id,
            "player_id": rp.player_id,
            "status": rp.status,
            "format": rp.format,
            "decision": ((rp.payload or {}).get("decisionLegacy") or (rp.payload or {}).get("decision") or rp.decision) if isinstance(rp.payload, dict) else rp.decision,
            "created_at": rp.created_at,
            "generated_by": rp.created_by,
            "pdf_available": rp.pdf_path is not None,
        }
        for rp in rps
    ]


@router.get("/cases/{case_id}/report-package/json")
async def download_report_json(
    case_id: str,
    rp_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    rp = await db.get(ReportPackage, rp_id)
    if not rp or str(rp.tenant_id) != str(current_user.tenant_id) or str(rp.case_id) != case_id:
        raise HTTPException(404, "ReportPackage não encontrado")
    await write_audit(db, current_user.tenant_id, current_user.id, "EXPORT_REPORT_JSON", "ReportPackage", rp_id)
    await db.commit()
    return rp.payload


@router.get("/cases/{case_id}/report-package/pdf", response_class=StreamingResponse)
async def download_report_pdf(
    case_id: str,
    rp_id: str,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Baixa o PDF de um ReportPackage específico.

    Parâmetros de query:
        rp_id: UUID do ReportPackage (obrigatório).

    Returns:
        application/pdf — bytes do relatório COAF.
    """
    rp = await db.get(ReportPackage, rp_id)
    if not rp or str(rp.tenant_id) != str(current_user.tenant_id) or str(rp.case_id) != case_id:
        raise HTTPException(404, "ReportPackage não encontrado")
    if rp.pdf_path is None:
        raise HTTPException(404, "PDF ainda não gerado para este pacote")
    import os as _os
    pdf_path_str = str(rp.pdf_path)
    if pdf_path_str.startswith("minio://"):
        try:
            from minio import Minio as _Minio
            minio_url = _os.getenv("MINIO_URL", "minio:9000")
            mc = _Minio(minio_url, access_key=_os.getenv("MINIO_ACCESS_KEY", "minio"),
                        secret_key=_os.getenv("MINIO_SECRET_KEY", "minio123"), secure=False)
            parts = pdf_path_str[len("minio://"):].split("/", 1)
            bucket, key = parts[0], parts[1]
            resp = mc.get_object(bucket, key)
            pdf_bytes = resp.read()
        except Exception as exc:
            logger.error("pdf_download_minio_failed", case_id=case_id, rp_id=rp_id, error=str(exc))
            raise HTTPException(503, "PDF temporariamente indisponível — tente novamente em instantes") from exc
    else:
        if not _os.path.isfile(pdf_path_str):
            raise HTTPException(404, "Arquivo PDF não encontrado no sistema")
        with open(pdf_path_str, "rb") as f:
            pdf_bytes = f.read()
    await write_audit(
        db,
        current_user.tenant_id,
        current_user.id,
        "EXPORT_REPORT_PDF",
        "ReportPackage",
        rp_id,
        after={"case_id": case_id},
    )
    await db.commit()
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{rp_id[:8]}.pdf"'},
    )


@router.get("/cases/{case_id}/report-package/xml", tags=["cases"])
@router.get("/cases/{case_id}/report-package/coaf-xml", tags=["cases"])
async def download_coaf_xml(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
):
    """Gera XML de Comunicação ao COAF conforme Resolução COAF 40/2021
    (formato COS v2.1 — Comunicado Siscoaf 97 / Portaria SPA/MF 1.143/2024).

    Busca o ReportPackage mais recente do caso para obter payload completo
    (narrativa, siscoaf codes, transações). O CPF é incluído sem máscara no
    XML conforme exige o COAF. O CNPJ do operador é lido de ``Tenant.cnpj``
    ou ``Tenant.settings["cnpj"]``.

    Returns:
        application/xml — arquivo para download/submissão ao Siscoaf.
    """

    case = await db.get(Case, case_id)
    if not case or str(case.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Case não encontrado")
    if case.status not in ("CLOSED", "REPORTED"):
        raise HTTPException(400, "Relatório COAF só pode ser gerado para cases CLOSED ou REPORTED")

    # ── Buscar o ReportPackage mais recente para extrair a narrativa ──────────
    rp = (await db.execute(
        select(ReportPackage)
        .where(
            ReportPackage.case_id == case_id,
            ReportPackage.tenant_id == current_user.tenant_id,
        )
        .order_by(ReportPackage.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    # Narrativa: preferir payload["analystNarrative"] → payload["analyst_narrative"] → coluna direta
    analyst_narrative = ""
    if rp is not None:
        analyst_narrative = str(
            (rp.payload or {}).get("analystNarrative")
            or (rp.payload or {}).get("analyst_narrative")
            or rp.analyst_narrative
            or ""
        )

    # ── Obter CNPJ do Tenant ──────────────────────────────────────────────────
    tenant = await db.get(Tenant, case.tenant_id)
    cnpj = "00000000000000"
    if tenant:
        cnpj = (
            getattr(tenant, "cnpj", None)
            or (tenant.settings or {}).get("cnpj")
            or "00000000000000"
        )
        if cnpj == "00000000000000":
            logger.warning(
                "tenant_cnpj_not_configured",
                tenant_id=case.tenant_id,
                hint="Configure settings['cnpj'] no cadastro do operador (COAF Res. 36/2021)",
            )

    # ── Buscar player ─────────────────────────────────────────────────────────
    player = await db.get(Player, case.player_id) if case.player_id is not None else None

    # ── Dados PII do sujeito — decifrados para submissão ao COAF ─────────────
    # COAF exige CPF sem máscara no XML (Res. COAF 40/2021).
    cpf_plain_xml: str | None = None
    name_plain_xml: str | None = None
    if player:
        cpf_plain_xml = decrypt_pii(player.cpf_encrypted)  # type: ignore[arg-type]
        name_plain_xml = decrypt_pii(player.name_encrypted)  # type: ignore[arg-type]

    # ── Payload do ReportPackage (ou payload mínimo se nenhum rp existe) ──────
    if rp is not None and isinstance(rp.payload, dict):
        rp_payload_for_xml: dict = rp.payload
    else:
        # Monta payload mínimo a partir dos dados do caso
        total_amount: float = 0.0
        if case.player_id is not None:
            total_amount = float((await db.execute(
                select(sqlfunc.coalesce(sqlfunc.sum(FinancialTransaction.amount), 0))
                .where(
                    FinancialTransaction.player_id == case.player_id,
                    FinancialTransaction.tenant_id == case.tenant_id,
                )
            )).scalar() or 0)
        rp_payload_for_xml = {
            "reportId": str(case_id),
            "analystNarrative": analyst_narrative,
            "generatedAt": datetime.now(UTC).isoformat(),
            "subject": {
                "birthDate": player.birth_date.isoformat() if player and player.birth_date else None,
                "pepFlag": bool(player.pep_flag) if player else False,
                "profession": player.profession if player else None,
                "declaredIncomeMonthly": float(player.declared_income_monthly or 0) if player else 0,
            },
            "financialSummary": {"totalDeposits90d": total_amount},
            "keyTransactions": [],
            "keyBets": [],
            "siscoaf": {
                "occurrence_codes": [],
                "involvement_types": [49],
                "valor_premio": 0.0,
                "valor_apostas": 0.0,
                "informacoes_adicionais": analyst_narrative,
                "portaria_referencia": "SPA/MF 1.143/2024",
                "comunicado_siscoaf": "97",
            },
        }

    # ── Gerar XML COAF/RIF via gerador alinhado à Res. COAF 40/2021 ──────────
    from coaf_xml import generate_coaf_xml  # noqa: PLC0415

    xml_bytes = generate_coaf_xml(
        rp_payload_for_xml,
        cpf_plain=cpf_plain_xml,
        name_plain=name_plain_xml,
        tenant_cnpj=cnpj,
        tenant_name=tenant.name if tenant else None,
    ).encode("utf-8")

    # Audit
    await write_audit(db, current_user.tenant_id, current_user.id,
                      "DOWNLOAD_COAF_XML", "Case", case_id)
    await db.commit()

    return FastAPIResponse(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=coaf_{case_id[:8]}.xml"},
    )


# ── Module 5: Comments with @mention + Link-Alert ─────────────────────────────

@router.post("/cases/{case_id}/comments", status_code=201)
async def add_case_comment(
    case_id: str,
    body: CaseCommentIn,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Adiciona comentário ao caso. Suporta @menção de analistas do mesmo tenant."""
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    valid_mentions = list(body.mentions or [])
    if body.mentions:
        try:
            result = await db.execute(
                select(User.id).where(
                    User.tenant_id == current_user.tenant_id,
                    User.id.in_(body.mentions),
                    User.active == True,  # noqa: E712
                    User.role.in_(["ADMIN", "AML_ANALYST", AppRole.GESTOR, AppRole.ANALISTA]),
                )
            )
            scalars_result = result.scalars()
            if inspect.isawaitable(scalars_result):
                scalars_result = await scalars_result
            all_result = scalars_result.all()
            if inspect.isawaitable(all_result):
                all_result = await all_result
            valid_mentions = list(all_result)
            if not valid_mentions and body.mentions:
                valid_mentions = list(body.mentions)
        except Exception:
            valid_mentions = list(body.mentions)
    evt = CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="NOTE",
        content={"comment": body.content, "mentions": valid_mentions, "author": current_user.id},
        created_by=current_user.id,
    )
    db.add(evt)
    for uid in valid_mentions:
        db.add(Notification(
            tenant_id=current_user.tenant_id,
            user_id=uid,
            type="CASE_MENTION",
            title=f"Você foi mencionado no caso: {c.title}",
            body=body.content[:200],
            reference_type="Case",
            reference_id=case_id,
        ))
    await write_audit(db, current_user.tenant_id, current_user.id,
                      "CASE_COMMENT", "Case", case_id,
                      after={"mentions": valid_mentions, "length": len(body.content)})
    await db.commit()
    await db.refresh(evt)
    return {"id": evt.id, "created_at": evt.created_at}


@router.post("/cases/{case_id}/link-alert")
async def link_alert_to_case(
    case_id: str,
    body: CaseLinkAlertIn,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Vincula um alerta avulso a um caso existente."""
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    a = await db.get(Alert, body.alert_id)
    if not a or str(a.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Alerta não encontrado")
    if c.player_id and a.player_id and str(c.player_id) != str(a.player_id):
        raise HTTPException(400, "Alerta pertence a outro jogador e não pode ser vinculado a este caso")
    if not c.player_id and a.player_id:
        c.player_id = a.player_id  # type: ignore[assignment]
    if not c.source_alert_id:
        c.source_alert_id = a.id  # type: ignore[assignment]
    await _ensure_case_reference_number(db, c)
    a.case_id = case_id  # type: ignore[assignment]
    db.add(CaseEvent(
        case_id=case_id, tenant_id=current_user.tenant_id,
        event_type="NOTE",
        content={
            "kind": "ALERT_LINKED",
            "alert_id": body.alert_id,
            "alert_title": a.title,
            "severity": a.severity,
            "case_reference_number": c.reference_number,
        },
        created_by=current_user.id,
    ))
    await write_audit(db, current_user.tenant_id, current_user.id,
                      "LINK_ALERT", "Case", case_id, after={"alert_id": body.alert_id})
    await db.commit()
    return {"case_id": case_id, "alert_id": body.alert_id, "status": "linked"}


@router.post("/cases/{case_id}/link-transaction")
async def link_transaction_to_case(
    case_id: str,
    body: CaseLinkTransactionIn,
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    tx = await db.get(FinancialTransaction, body.transaction_id)
    if not tx or str(tx.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Transação não encontrada")
    db.add(CaseEvent(
        case_id=case_id,
        tenant_id=current_user.tenant_id,
        event_type="NOTE",
        content={
            "kind": "TRANSACTION_LINKED",
            "transaction_id": body.transaction_id,
            "type": str(tx.type),
            "amount": float(tx.amount or 0),
            "occurred_at": tx.occurred_at.isoformat() if tx.occurred_at else None,
        },
        created_by=current_user.id,
    ))
    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "LINK_TRANSACTION", "Case", case_id,
        after={"transaction_id": body.transaction_id},
    )
    await db.commit()
    return {"case_id": case_id, "transaction_id": body.transaction_id, "status": "linked"}


@router.get("/cases/{case_id}/lookup")
async def lookup_case_entities(
    case_id: str,
    q: str = Query(..., min_length=2),
    scope: str = Query("all", pattern="^(all|alerts|transactions)$"),
    limit: int = Query(10, ge=1, le=50),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")

    alerts_payload = []
    tx_payload = []
    term = f"%{q}%"

    if scope in {"all", "alerts"}:
        alerts = (await db.execute(
            select(Alert)
            .where(
                Alert.tenant_id == current_user.tenant_id,
                Alert.case_id.is_(None),
                sqlfunc.lower(Alert.title).like(sqlfunc.lower(term)),
            )
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )).scalars().all()
        alerts_payload = [
            {
                "id": alert.id,
                "title": alert.title,
                "severity": alert.severity,
                "created_at": alert.created_at,
            }
            for alert in alerts
        ]

    if scope in {"all", "transactions"}:
        txns = (await db.execute(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.tenant_id == current_user.tenant_id,
                FinancialTransaction.player_id == c.player_id if c.player_id is not None else True,
            )
            .order_by(FinancialTransaction.occurred_at.desc())
            .limit(100)
        )).scalars().all()
        lowered = q.lower()
        matched_txns = []
        for tx in txns:
            haystack = " ".join([
                str(tx.id),
                str(getattr(tx, "type", "")),
                str(getattr(tx, "status", "")),
                str(getattr(tx, "payment_instrument", "")),
                str(getattr(tx, "description", "")),
            ]).lower()
            if lowered in haystack:
                matched_txns.append(tx)
            if len(matched_txns) >= limit:
                break
        tx_payload = [
            {
                "id": tx.id,
                "type": tx.type,
                "amount": float(tx.amount or 0),
                "status": tx.status,
                "occurred_at": tx.occurred_at,
            }
            for tx in matched_txns
        ]

    return {"alerts": alerts_payload, "transactions": tx_payload}


# ─────────────────────────────────────────────────────────────────────────────
# GAP-T2 + GAP-C1: Timeline narrativa do caso com janela de evidências
# Retorna blocos cronológicos de transações + apostas + devices + alertas
# cobrindo a janela de 90 dias ao redor da data de criação do caso.
# Serve como insumo para preenchimento do dossiê COAF (Siscoaf 97).
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/cases/{case_id}/timeline")
async def get_case_timeline(
    case_id: str,
    window_days: int = Query(90, ge=1, le=365),
    include: str = Query("transactions,bets,devices,alerts,case_events"),
    current_user: User = Depends(require_role_any([AppRole.ANALISTA, AppRole.GESTOR])),
    db: AsyncSession = Depends(get_db),
):
    """Timeline de evidências do caso cobrindo a janela em torno da data de criação.

    Diferentemente de /players/{id}/timeline (centrado no player, sem limite de caso),
    este endpoint:
    - ancora a janela na data de criação do caso (case.created_at)
    - inclui case_events (audit trail do caso) junto com eventos do player
    - retorna sumário de risk_score ao longo da janela (evolução do composite_score)
    """
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    if not c.player_id:
        raise HTTPException(422, "Caso sem player vinculado — timeline não disponível")

    player_id = str(c.player_id)
    # Janela centrada no caso: window_days antes + 7 dias após criação
    case_created = c.created_at
    window_start = case_created - timedelta(days=window_days)
    window_end   = case_created + timedelta(days=7)

    include_set = {s.strip().lower() for s in include.split(",")}
    events: list[dict] = []

    # ── Case events (audit trail) ────────────────────────────────────────────
    if "case_events" in include_set:
        cevs = (await db.execute(
            select(CaseEvent)
            .where(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.created_at)
        )).scalars().all()
        for ce in cevs:
            events.append({
                "ts": ce.created_at,
                "type": "CASE_EVENT",
                "subtype": ce.event_type,
                "content": ce.content,
                "id": str(ce.id),
            })

    # ── Alertas do player na janela ─────────────────────────────────────────
    if "alerts" in include_set:
        alts = (await db.execute(
            select(Alert)
            .where(
                Alert.tenant_id == current_user.tenant_id,
                Alert.player_id == player_id,
                Alert.created_at >= window_start,
                Alert.created_at <= window_end,
            )
            .order_by(Alert.created_at)
        )).scalars().all()
        for a in alts:
            events.append({
                "ts": a.created_at,
                "type": "ALERT",
                "subtype": a.alert_type,
                "severity": a.severity,
                "title": a.title,
                "composite_score": float(a.composite_score) if a.composite_score is not None else None,
                "ingest_mode": a.ingest_mode,
                "is_case_alert": str(a.case_id) == case_id if a.case_id else False,
                "id": str(a.id),
            })

    # ── Transações do player na janela ──────────────────────────────────────
    if "transactions" in include_set:
        txns = (await db.execute(
            select(FinancialTransaction)
            .where(
                FinancialTransaction.tenant_id == current_user.tenant_id,
                FinancialTransaction.player_id == player_id,
                FinancialTransaction.occurred_at >= window_start,
                FinancialTransaction.occurred_at <= window_end,
            )
            .order_by(FinancialTransaction.occurred_at)
        )).scalars().all()
        for t in txns:
            events.append({
                "ts": t.occurred_at,
                "type": "TRANSACTION",
                "subtype": t.type,
                "amount": float(t.amount) if t.amount is not None else None,
                "currency": t.currency,
                "method": t.payment_method,
                "status": t.status,
                "id": str(t.id),
            })

    # ── Apostas do player na janela ─────────────────────────────────────────
    if "bets" in include_set:
        bets = (await db.execute(
            select(Bet)
            .where(
                Bet.tenant_id == current_user.tenant_id,
                Bet.player_id == player_id,
                Bet.occurred_at >= window_start,
                Bet.occurred_at <= window_end,
            )
            .order_by(Bet.occurred_at)
        )).scalars().all()
        for b in bets:
            events.append({
                "ts": b.occurred_at,
                "type": "BET",
                "subtype": b.bet_type,
                "amount": float(b.stake_amount) if b.stake_amount is not None else None,
                "odds": float(b.odds) if b.odds is not None else None,
                "settled_payout": float(b.actual_payout) if b.actual_payout is not None else None,
                "sport": b.event_name,
                "status": b.status,
                "id": str(b.id),
            })

    # ── Device events do player na janela ───────────────────────────────────
    if "devices" in include_set:
        devs = (await db.execute(
            select(DeviceEvent)
            .where(
                DeviceEvent.tenant_id == current_user.tenant_id,
                DeviceEvent.player_id == player_id,
                DeviceEvent.occurred_at >= window_start,
                DeviceEvent.occurred_at <= window_end,
            )
            .order_by(DeviceEvent.occurred_at)
        )).scalars().all()
        for d in devs:
            events.append({
                "ts": d.occurred_at,
                "type": "DEVICE_EVENT",
                "subtype": d.action,
                "device_id": d.device_id,
                "country": d.country_code,
                "id": str(d.id),
            })

    # Ordenar e agrupar por dia
    events.sort(key=lambda e: e["ts"] or datetime.now(UTC))
    days: dict[str, list[dict]] = {}
    for ev in events:
        ts = ev["ts"]
        day_key = ts.strftime("%Y-%m-%d") if ts else "unknown"
        days.setdefault(day_key, []).append({**ev, "ts": ts.isoformat() if ts else None})

    # Sumarizar evolução do risk score ao longo da janela (GAP-C1)
    score_series = [
        {"ts": ev["ts"].isoformat() if ev["ts"] else None, "composite_score": ev["composite_score"]}
        for ev in events
        if ev["type"] == "ALERT" and ev.get("composite_score") is not None
    ]

    # Totais por tipo para o cabeçalho do dossiê
    type_counts: dict[str, int] = {}
    for ev in events:
        type_counts[ev["type"]] = type_counts.get(ev["type"], 0) + 1

    await write_audit(
        db, current_user.tenant_id, current_user.id,
        "GET_CASE_TIMELINE", "Case", case_id,
    )
    await db.flush()

    return {
        "case_id": case_id,
        "player_id": player_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_days": window_days,
        "total_events": len(events),
        "event_summary": type_counts,
        "score_series": score_series,
        "backfill_job_id": getattr(c, "backfill_job_id", None),
        "ingest_mode": getattr(c, "ingest_mode", "incremental"),
        "timeline": [
            {"date": day, "event_count": len(blk), "events": blk}
            for day, blk in sorted(days.items())
        ],
    }

