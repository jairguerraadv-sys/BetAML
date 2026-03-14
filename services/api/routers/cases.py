"""routers/cases.py — Case management: CRUD, assign, events, evidence, report-package."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response as FastAPIResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decrypt_pii, get_current_user, mask_cpf, require_roles
from database import get_db
from models import Alert, Case, CaseEvent, FinancialTransaction, Player, ReportPackage, Tenant, User
from repositories import CaseRepository
from repositories.cases import get_case_repo
from utils import write_audit

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
    doc.build(story)
    return buf.getvalue()


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


class CaseEventCreate(BaseModel):
    event_type: str = "NOTE"
    content: dict[str, Any]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/cases", status_code=201)
async def create_case(
    body: CaseCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = Case(
        tenant_id=current_user.tenant_id,
        player_id=body.player_id,
        title=body.title,
        description=body.description,
        severity=body.severity,
        created_by=current_user.id,
    )
    db.add(c)
    await db.flush()
    await write_audit(db, current_user.tenant_id, current_user.id, "CREATE", "Case", c.id, after=body.model_dump())
    await db.commit()
    await db.refresh(c)
    return {"id": c.id, "title": c.title, "status": c.status}


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
    return [
        {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "player_id": c.player_id, "assigned_to": c.assigned_to, "created_at": c.created_at,
            "reference_number": getattr(c, "reference_number", None),
            "priority": getattr(c, "priority", "MEDIUM"),
            "sla_due_at": getattr(c, "sla_due_at", None),
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
    alerts = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events = (await db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at)
    )).scalars().all()
    return {
        "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
        "description": c.description, "player_id": c.player_id,
        "assigned_to": c.assigned_to, "created_at": c.created_at,
        "reference_number": getattr(c, "reference_number", None),
        "priority": getattr(c, "priority", "MEDIUM"),
        "sla_due_at": getattr(c, "sla_due_at", None),
        "alerts": [{"id": a.id, "severity": a.severity, "title": a.title} for a in alerts],
        "timeline": [
            {"id": e.id, "event_type": e.event_type, "content": e.content, "created_at": e.created_at}
            for e in events
        ],
    }


@router.post("/cases/{case_id}/assign")
async def assign_case(
    case_id: str,
    body: AssignRequest,
    current_user: User = Depends(require_roles("ADMIN")),
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
    await write_audit(db, current_user.tenant_id, current_user.id, "ASSIGN", "Case", case_id, after={"assigned_to": body.user_id})
    await db.commit()
    return {"case_id": case_id, "assigned_to": body.user_id}


@router.post("/cases/{case_id}/events", status_code=201)
async def add_case_event(
    case_id: str,
    body: CaseEventCreate,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
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
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
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
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or str(c.tenant_id) != str(current_user.tenant_id):
        raise HTTPException(404, "Caso não encontrado")
    if body.decision == "FILE_SAR" and not body.analyst_narrative:
        raise HTTPException(400, "analyst_narrative é obrigatório quando decision=FILE_SAR (COAF Res. 36/2021 Art. 9)")

    alerts  = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events  = (await db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id))).scalars().all()

    player_info: dict = {}
    if c.player_id is not None:
        p = await db.get(Player, c.player_id)
        if p:
            cpf_plain = decrypt_pii(p.cpf_encrypted)  # type: ignore[arg-type]
            player_info = {
                "player_id": p.id, "external_player_id": p.external_player_id,
                "cpf_masked": mask_cpf(cpf_plain), "pep_flag": p.pep_flag,
                "risk_score": float(p.risk_score),  # type: ignore[arg-type]
            }

    alert_amounts = [float(a.evidence.get("amount", 0)) for a in alerts if isinstance(a.evidence, dict)]
    financial_summary = {
        "total_alerts":          len(alerts),
        "total_amount_brl":      round(sum(alert_amounts), 2),
        "max_single_amount_brl": round(max(alert_amounts, default=0.0), 2),
        "alert_types":           list({a.alert_type for a in alerts if a.alert_type is not None}),
    }
    report_id    = str(uuid.uuid4())
    generated_at = datetime.now(UTC).isoformat()
    payload = {
        "report_id": report_id, "schema_version": "1.0",
        "generated_at": generated_at, "generated_by": current_user.id,
        "reporting_entity": {"tenant_id": current_user.tenant_id, "platform": "BetAML"},
        "subject": player_info,
        "case": {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "opened_at": c.created_at.isoformat() + "Z" if c.created_at is not None else None,
        },
        "suspicious_operations": [
            {"alert_id": a.id, "title": a.title, "severity": a.severity,
             "alert_type": a.alert_type, "evidence": a.evidence,
             "occurred_at": a.created_at.isoformat() + "Z" if a.created_at is not None else None}
            for a in alerts
        ],
        "financial_summary": financial_summary,
        "investigation_timeline": [
            {"event_type": e.event_type, "content": e.content,
             "recorded_at": e.created_at.isoformat() + "Z" if e.created_at is not None else None}
            for e in events
        ],
        "analyst_narrative": body.analyst_narrative,
        "decision": body.decision or "PENDING",
        "decision_basis": "Análise conforme COAF Res. 36/2021 e regulamentação Bacen/MF",
    }

    rp = ReportPackage(
        tenant_id=current_user.tenant_id, case_id=case_id, player_id=c.player_id,
        payload=payload, analyst_narrative=body.analyst_narrative,
        decision=None,  # business decision stored in JSONB payload, not in DB enum column
        status="DRAFT" if body.decision in (None, "PENDING") else "FINAL",
        created_by=current_user.id,
    )
    db.add(rp)

    pdf_path: str | None = None
    try:
        pdf_bytes = await asyncio.get_event_loop().run_in_executor(None, _build_report_pdf, payload)
        if pdf_bytes:
            import os as _os
            import tempfile as _tmp
            pdf_filename = f"reports/{current_user.tenant_id}/{report_id}.pdf"
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
                tmp_path = _os.path.join(_tmp.gettempdir(), f"{report_id}.pdf")
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
        content={"report_id": report_id, "decision": body.decision},
        created_by=current_user.id,
    ))
    await write_audit(db, current_user.tenant_id, current_user.id, "GENERATE_REPORT", "Case", case_id,
                      after={"report_id": report_id, "decision": body.decision})
    await db.commit()
    await db.refresh(rp)
    return {
        "report_package_id": rp.id, "status": rp.status,
        "decision": payload.get("decision", "PENDING"),
        "pdf_path": rp.pdf_path, "payload": payload,
    }


@router.post("/cases/{case_id}/report-package/submit")
async def submit_report_package(
    case_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
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

    payload_decision = (rp.payload or {}).get("decision", "PENDING") if isinstance(rp.payload, dict) else "PENDING"
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
        after={"report_package_id": rp.id, "tracking_id": tracking_id},
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
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST", "AUDITOR")),
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


@router.get("/cases/{case_id}/report-package/pdf", response_class=StreamingResponse)
async def download_report_pdf(
    case_id: str,
    rp_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
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
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{rp_id[:8]}.pdf"'},
    )


@router.get("/cases/{case_id}/report-package/coaf-xml", tags=["cases"])
async def download_coaf_xml(
    case_id: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(require_roles("ADMIN", "AML_ANALYST")),
):
    """Gera XML de Comunicação ao COAF conforme Resolução COAF 36/2021.

    Busca o ReportPackage mais recente do caso para obter a narrativa do analista.
    O CNPJ do operador é lido de Tenant.settings["cnpj"]; se ausente, usa
    "00000000000000" e registra warning no log.

    Returns:
        application/xml — arquivo para download.
    """
    from xml.etree.ElementTree import Element, SubElement, tostring
    from xml.dom import minidom

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

    # ── Montar XML conforme schema COAF RIF ───────────────────────────────────
    root = Element("ComunicacaoMifd")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("versao", "3.0")

    # Cabeçalho
    cabecalho = SubElement(root, "Cabecalho")
    SubElement(cabecalho, "NumeroSequencial").text = str(
        getattr(case, "reference_number", None) or case_id[-8:]
    )
    SubElement(cabecalho, "DataHoraEnvio").text = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    SubElement(cabecalho, "TipoOperacao").text = "I"  # Inclusão

    # Comunicante (Operador)
    comunicante = SubElement(root, "Comunicante")
    SubElement(comunicante, "CnpjOuCpfComunicante").text = cnpj
    SubElement(comunicante, "NomeComunicante").text = str(
        (tenant.name if tenant else None) or current_user.tenant_id
    )
    SubElement(comunicante, "TipoAtividade").text = "APOSTAS"

    # Parte Envolvida
    if player:
        partes = SubElement(root, "PartesEnvolvidas")
        parte = SubElement(partes, "Parte")
        SubElement(parte, "TipoPessoa").text = "F"  # Física
        SubElement(parte, "NomePessoa").text = "CONFIDENCIAL"  # PII protegida por LGPD
        SubElement(parte, "TipoPapel").text = "COMUNICADO"
        # CPF mascarado — obrigatório COAF Res. 36/2021 Schema MIFD v3
        cpf_plain = decrypt_pii(player.cpf_encrypted)  # type: ignore[arg-type]
        SubElement(parte, "CpfCnpjPessoa").text = mask_cpf(cpf_plain)

    # Operação Suspeita
    # Buscar total de transações do player (ValorOperacao — obrigatório MIFD v3)
    total_amount: float = 0.0
    if case.player_id is not None:
        total_amount = float((await db.execute(
            select(sqlfunc.coalesce(sqlfunc.sum(FinancialTransaction.amount), 0))
            .where(
                FinancialTransaction.player_id == case.player_id,
                FinancialTransaction.tenant_id == case.tenant_id,
            )
        )).scalar() or 0)

    operacoes = SubElement(root, "Operacoes")
    operacao = SubElement(operacoes, "Operacao")
    SubElement(operacao, "NumeroOperacao").text = str(case_id[-8:])
    SubElement(operacao, "DataOperacao").text = case.created_at.strftime("%Y-%m-%d")
    SubElement(operacao, "NaturezaOperacao").text = "APOSTA_ESPORTIVA"
    SubElement(operacao, "ValorOperacao").text = f"{total_amount:.2f}"
    SubElement(operacao, "DescricaoSuspeita").text = analyst_narrative

    # Serializar com pretty print
    raw = tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="UTF-8")

    # Audit
    await write_audit(db, current_user.tenant_id, current_user.id,
                      "DOWNLOAD_COAF_XML", "Case", case_id)
    await db.commit()

    return FastAPIResponse(
        content=pretty if isinstance(pretty, bytes) else pretty.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=coaf_{case_id[:8]}.xml"},
    )
