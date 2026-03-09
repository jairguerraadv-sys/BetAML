"""routers/cases.py — Case management: CRUD, assign, events, evidence, report-package."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decrypt_pii, get_current_user, mask_cpf, require_roles
from database import get_db
from models import Alert, Case, CaseEvent, Player, ReportPackage, User
from utils import write_audit

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["cases"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_report_pdf(payload: dict) -> bytes:
    """Gera PDF COAF compacto via reportlab. Retorna bytes (vazio se lib indisponível)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, Spacer, SimpleDocTemplate, Table, TableStyle
        from reportlab.lib import colors
    except ImportError:
        return b""

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
    db: AsyncSession = Depends(get_db),
):
    q = select(Case).where(Case.tenant_id == current_user.tenant_id)
    if status_filter: q = q.where(Case.status == status_filter)
    if player_id:     q = q.where(Case.player_id == player_id)
    q = q.order_by(Case.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    cases = result.scalars().all()
    return [
        {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "player_id": c.player_id, "assigned_to": c.assigned_to, "created_at": c.created_at,
        }
        for c in cases
    ]


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    c = await db.get(Case, case_id)
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    alerts = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events = (await db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.created_at)
    )).scalars().all()
    return {
        "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
        "description": c.description, "player_id": c.player_id,
        "assigned_to": c.assigned_to, "created_at": c.created_at,
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
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    c.assigned_to = body.user_id
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
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    if body.event_type == "STATUS_CHANGE":
        new_status = body.content.get("new_status")
        if new_status:
            c.status = new_status
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
    if not c or c.tenant_id != current_user.tenant_id:
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
    if not c or c.tenant_id != current_user.tenant_id:
        raise HTTPException(404, "Caso não encontrado")
    if body.decision == "FILE_SAR" and not body.analyst_narrative:
        raise HTTPException(400, "analyst_narrative é obrigatório quando decision=FILE_SAR (COAF Res. 36/2021 Art. 9)")

    alerts  = (await db.execute(select(Alert).where(Alert.case_id == case_id))).scalars().all()
    events  = (await db.execute(select(CaseEvent).where(CaseEvent.case_id == case_id))).scalars().all()

    player_info: dict = {}
    if c.player_id:
        p = await db.get(Player, c.player_id)
        if p:
            cpf_plain = decrypt_pii(p.cpf_encrypted)
            player_info = {
                "player_id": p.id, "external_player_id": p.external_player_id,
                "cpf_masked": mask_cpf(cpf_plain), "pep_flag": p.pep_flag,
                "risk_score": float(p.risk_score),
            }

    alert_amounts = [float(a.evidence.get("amount", 0)) for a in alerts if isinstance(a.evidence, dict)]
    financial_summary = {
        "total_alerts":          len(alerts),
        "total_amount_brl":      round(sum(alert_amounts), 2),
        "max_single_amount_brl": round(max(alert_amounts, default=0.0), 2),
        "alert_types":           list({a.alert_type for a in alerts if a.alert_type}),
    }
    report_id    = str(uuid.uuid4())
    generated_at = datetime.utcnow().isoformat() + "Z"
    payload = {
        "report_id": report_id, "schema_version": "1.0",
        "generated_at": generated_at, "generated_by": current_user.id,
        "reporting_entity": {"tenant_id": current_user.tenant_id, "platform": "BetAML"},
        "subject": player_info,
        "case": {
            "id": c.id, "title": c.title, "status": c.status, "severity": c.severity,
            "opened_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        },
        "suspicious_operations": [
            {"alert_id": a.id, "title": a.title, "severity": a.severity,
             "alert_type": a.alert_type, "evidence": a.evidence,
             "occurred_at": a.created_at.isoformat() + "Z" if a.created_at else None}
            for a in alerts
        ],
        "financial_summary": financial_summary,
        "investigation_timeline": [
            {"event_type": e.event_type, "content": e.content,
             "recorded_at": e.created_at.isoformat() + "Z" if e.created_at else None}
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
            import os as _os, tempfile as _tmp
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
            rp.pdf_path = pdf_path
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


@router.get("/cases/{case_id}/report-package/{rp_id}/pdf")
async def download_report_pdf(
    case_id: str,
    rp_id: str,
    current_user: User = Depends(require_roles("ADMIN", "AML_ANALYST")),
    db: AsyncSession = Depends(get_db),
):
    rp = await db.get(ReportPackage, rp_id)
    if not rp or rp.tenant_id != current_user.tenant_id or rp.case_id != case_id:
        raise HTTPException(404, "ReportPackage não encontrado")
    if not rp.pdf_path:
        raise HTTPException(404, "PDF ainda não gerado para este pacote")
    import os as _os
    if rp.pdf_path.startswith("minio://"):
        try:
            from minio import Minio as _Minio
            minio_url = _os.getenv("MINIO_URL", "minio:9000")
            mc = _Minio(minio_url, access_key=_os.getenv("MINIO_ACCESS_KEY", "minio"),
                        secret_key=_os.getenv("MINIO_SECRET_KEY", "minio123"), secure=False)
            parts = rp.pdf_path[len("minio://"):].split("/", 1)
            bucket, key = parts[0], parts[1]
            resp = mc.get_object(bucket, key)
            pdf_bytes = resp.read()
        except Exception as exc:
            raise HTTPException(500, f"Erro ao ler PDF do MinIO: {exc}") from exc
    else:
        if not _os.path.isfile(rp.pdf_path):
            raise HTTPException(404, "Arquivo PDF não encontrado no sistema")
        with open(rp.pdf_path, "rb") as f:
            pdf_bytes = f.read()
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report_{rp_id[:8]}.pdf"'},
    )
