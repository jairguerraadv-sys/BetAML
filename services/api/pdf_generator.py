"""
pdf_generator.py — Geração de PDF para ReportPackage COAF.

A função build_report_pdf() é CPU-bound (usa ReportLab) e deve ser
chamada via event_loop.run_in_executor() para não bloquear o event loop.
"""
from __future__ import annotations

import io
from typing import Any


def build_report_pdf(payload: dict[str, Any]) -> bytes:
    """
    Gera um PDF compacto de ReportPackage com as seções COAF (Res. 36/2021).
    Retorna bytes do PDF ou b"" se ReportLab não estiver disponível.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError:
        return b""

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
    )
    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    normal = styles["Normal"]
    small = ParagraphStyle("small", parent=normal, fontSize=8)

    story = []

    story.append(Paragraph("BetAML — Pacote de Investigação COAF", h1))
    story.append(Paragraph(
        f"Relatório: {payload.get('report_id', '')} &nbsp;|&nbsp; "
        f"Gerado: {payload.get('generated_at', '')}",
        small,
    ))
    story.append(Spacer(1, 0.4*cm))

    # Decisão
    decision_color = {"FILE_SAR": "#cc0000", "NO_ACTION": "#006600", "PENDING": "#cc6600"}
    decision = payload.get("decision", "PENDING")
    story.append(Paragraph(
        f"Decisão: <b><font color='{decision_color.get(decision, '#000')}'>{decision}</font></b>",
        normal,
    ))
    if payload.get("analyst_narrative"):
        story.append(Paragraph(f"Narrativa analítica: {payload['analyst_narrative']}", normal))
    story.append(Spacer(1, 0.3*cm))

    # Sujeito
    story.append(Paragraph("Sujeito da Operação", h2))
    subject = payload.get("subject", {})
    subj_data = [["Campo", "Valor"]] + [
        [str(k), str(v)] for k, v in subject.items() if v is not None
    ]
    if len(subj_data) > 1:
        t = Table(subj_data, colWidths=[5*cm, 12*cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("GRID",       (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3*cm))

    # Caso
    story.append(Paragraph("Caso de Investigação", h2))
    case_info = payload.get("case", {})
    story.append(Paragraph(
        f"<b>{case_info.get('title', '')}</b> | "
        f"Status: {case_info.get('status', '')} | "
        f"Severidade: {case_info.get('severity', '')}",
        normal,
    ))
    story.append(Spacer(1, 0.3*cm))

    # Operações suspeitas
    story.append(Paragraph("Operações Suspeitas (Alertas)", h2))
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
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTSIZE",   (0, 0), (-1, -1), 7),
            ("GRID",       (0, 0), (-1, -1), 0.3, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8f9fa"), colors.white]),
        ]))
        story.append(t)
    story.append(Spacer(1, 0.3*cm))

    # Resumo financeiro
    story.append(Paragraph("Resumo Financeiro", h2))
    fin = payload.get("financial_summary", {})
    story.append(Paragraph(
        f"Total de alertas: {fin.get('total_alerts', 0)} | "
        f"Valor total BRL: {fin.get('total_amount_brl', 0):.2f} | "
        f"Maior valor único: {fin.get('max_single_amount_brl', 0):.2f}",
        normal,
    ))

    doc.build(story)
    return buf.getvalue()
