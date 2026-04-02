"""coaf_xml.py — Gerador de XML COAF/RIF alinhado à Resolução COAF 40/2021
e Portaria SPA/MF 1.143/2024 (Comunicado Siscoaf 97).

Referências:
  - Resolução COAF nº 40, de 30 de julho de 2021
  - Portaria SPA/MF nº 1.143/2024 (apostas esportivas)
  - Comunicado Siscoaf nº 97 (30/12/2024 — vigência 01/04/2025)
  - Estrutura XML baseada no leiaute Siscoaf v2.1

Uso:
    xml_str = generate_coaf_xml(
        payload=report_package.payload,
        cpf_plain="12345678900",
        name_plain="João da Silva",
        tenant_cnpj=tenant.cnpj,
        tenant_name=tenant.name,
    )
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc

# Código de tipo de comunicante para operadoras de apostas (Portaria SPA/MF 1.143/2024)
_TIPO_COMUNICANTE = "5"
_SCHEMA_VERSAO = "2.1"

# Máximo de transações/apostas incluídas no XML para evitar payloads gigantes
_MAX_TXNS = 50
_MAX_BETS = 20


def _sub(parent: ET.Element, tag: str, text: str | None = None) -> ET.Element:
    """Cria sub-elemento, garantindo que text seja string válida para XML."""
    el = ET.SubElement(parent, tag)
    if text is not None:
        # Remove caracteres de controle XML-inválidos (0x00–0x08, 0x0B, 0x0C, 0x0E–0x1F)
        clean = "".join(c for c in str(text) if ord(c) >= 0x20 or c in "\t\n\r")
        el.text = clean
    return el


def _date_str(iso: str | None, fallback: datetime) -> str:
    """Extrai YYYY-MM-DD de string ISO ou usa fallback."""
    if iso:
        return str(iso)[:10]
    return fallback.strftime("%Y-%m-%d")


def generate_coaf_xml(
    payload: dict[str, Any],
    *,
    cpf_plain: str | None,
    name_plain: str | None,
    tenant_cnpj: str | None,
    tenant_name: str | None,
) -> str:
    """Gera XML de Comunicação de Operações Suspeitas (COS) no formato COAF v2.1.

    Args:
        payload: Payload completo do ReportPackage (campo ``payload`` do ORM).
        cpf_plain: CPF decifrado (somente dígitos, 11 chars) do sujeito.
        name_plain: Nome completo decifrado.
        tenant_cnpj: CNPJ do operador de apostas (14 dígitos).
        tenant_name: Razão social do operador.

    Returns:
        XML completo como string Unicode, incluindo declaração ``<?xml …>``.
        Pronto para ser gravado em arquivo .xml ou submetido ao Siscoaf.
    """
    now = datetime.now(UTC)
    report_id = str(payload.get("reportId") or payload.get("report_id") or "")
    generated_at_raw = payload.get("generatedAt") or payload.get("generated_at") or now.isoformat()

    try:
        generated_at = datetime.fromisoformat(str(generated_at_raw).replace("Z", "+00:00"))
    except Exception:
        generated_at = now

    siscoaf: dict = payload.get("siscoaf") or {}
    subject: dict = payload.get("subject") or {}
    financial: dict = payload.get("financialSummary") or payload.get("financial_summary") or {}
    key_txns: list = payload.get("keyTransactions") or []
    key_bets: list = payload.get("keyBets") or []

    occurrence_codes: list[int] = [int(c) for c in (siscoaf.get("occurrence_codes") or [])]
    involvement_types: list[int] = [int(t) for t in (siscoaf.get("involvement_types") or [49])]
    valor_premio = float(siscoaf.get("valor_premio") or 0.0)
    valor_apostas = float(siscoaf.get("valor_apostas") or 0.0)
    info_adicionais = str(siscoaf.get("informacoes_adicionais") or "")
    portaria = str(siscoaf.get("portaria_referencia") or "SPA/MF 1.143/2024")

    # ── Root ──────────────────────────────────────────────────────────────────
    root = ET.Element("ComunicacaoOperacoesSuspeitas")
    root.set("versao", _SCHEMA_VERSAO)
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")

    # ── Cabeçalho ─────────────────────────────────────────────────────────────
    cab = _sub(root, "Cabecalho")
    _sub(cab, "Versao", _SCHEMA_VERSAO)
    # CodigoRegistro: identificador único desta submissão (máx 36 chars)
    _sub(cab, "CodigoRegistro", f"BETAML-{report_id[:28]}")
    _sub(cab, "DataCriacao", generated_at.strftime("%Y-%m-%d"))
    _sub(cab, "HoraCriacao", generated_at.strftime("%H:%M:%S"))
    _sub(cab, "TipoComunicante", _TIPO_COMUNICANTE)
    _sub(cab, "NomeComunicante", (tenant_name or "Operadora de Apostas")[:100])
    cnpj_digits = "".join(c for c in (tenant_cnpj or "") if c.isdigit())
    _sub(cab, "CNPJComunicante", cnpj_digits.ljust(14, "0")[:14])

    # ── Comunicacao ───────────────────────────────────────────────────────────
    com = _sub(root, "Comunicacao")
    # CodigoComunicacao: referência interna do analista (máx 36 chars)
    _sub(com, "CodigoComunicacao", f"RPT-{report_id[:32]}")
    _sub(com, "DataComunicacao", generated_at.strftime("%Y-%m-%d"))

    # ── Pessoa ────────────────────────────────────────────────────────────────
    pessoa = _sub(com, "Pessoa")
    _sub(pessoa, "TipoPessoa", "F")  # F = Pessoa Física

    # CPF: 11 dígitos sem máscara (COAF exige documento completo)
    cpf_digits = "".join(c for c in (cpf_plain or "") if c.isdigit())
    _sub(pessoa, "CPF", cpf_digits[:11].ljust(11, "0"))

    _sub(pessoa, "NomeCompleto", (name_plain or "NÃO IDENTIFICADO")[:100])

    birth_date = _date_str(str(subject.get("birthDate") or ""), generated_at) if subject.get("birthDate") else None
    if birth_date:
        _sub(pessoa, "DataNascimento", birth_date)

    _sub(pessoa, "PoliticamenteExposta", "S" if subject.get("pepFlag") else "N")

    profession = str(subject.get("profession") or "")
    if profession:
        _sub(pessoa, "Profissao", profession[:80])

    income = float(subject.get("declaredIncomeMonthly") or 0)
    if income > 0:
        _sub(pessoa, "RendaMensalDeclarada", f"{income:.2f}")

    # TiposEnvolvimento (Siscoaf 97: 1=Titular, 8=Outros, 49=Apostador, 50=Usuário)
    env_el = _sub(pessoa, "TiposEnvolvimento")
    for tipo in involvement_types:
        _sub(env_el, "TipoEnvolvimento", str(tipo))

    # ── Operações Suspeitas ───────────────────────────────────────────────────
    ops = _sub(com, "OperacoesSuspeitas")

    txn_count = 0
    for txn in key_txns[:_MAX_TXNS]:
        op = _sub(ops, "Operacao")
        tipo_op = str(txn.get("type") or "DESCONHECIDO").upper()[:30]
        _sub(op, "TipoOperacao", tipo_op)
        _sub(op, "DataOperacao", _date_str(str(txn.get("occurredAt") or ""), generated_at))
        _sub(op, "ValorOperacao", f"{float(txn.get('amount') or 0):.2f}")
        _sub(op, "MoedaOperacao", "BRL")
        instr = str(txn.get("paymentInstrument") or "")
        if instr:
            _sub(op, "InstrumentoPagamento", instr[:50])
        txn_count += 1

    for bet in key_bets[:_MAX_BETS]:
        op = _sub(ops, "Operacao")
        _sub(op, "TipoOperacao", "APOSTA")
        _sub(op, "DataOperacao", _date_str(str(bet.get("occurredAt") or ""), generated_at))
        _sub(op, "ValorOperacao", f"{float(bet.get('stakeAmount') or 0):.2f}")
        _sub(op, "MoedaOperacao", "BRL")

    # Se não há eventos individuais, inclui totalizador do período
    if txn_count == 0 and not key_bets:
        op = _sub(ops, "Operacao")
        total_dep = float(financial.get("totalDeposits90d") or 0)
        _sub(op, "TipoOperacao", "MOVIMENTACAO_SUSPEITA")
        _sub(op, "DataOperacao", generated_at.strftime("%Y-%m-%d"))
        _sub(op, "ValorOperacao", f"{total_dep:.2f}")
        _sub(op, "MoedaOperacao", "BRL")
        _sub(op, "Observacao", "Valor totalizado 90d — operações individuais não disponíveis")

    # ── Siscoaf (Portaria SPA/MF 1.143/2024 / Comunicado 97) ─────────────────
    sisc_el = _sub(com, "Siscoaf")
    _sub(sisc_el, "PortariaReferencia", portaria)
    _sub(sisc_el, "ComunicadoSiscoaf", str(siscoaf.get("comunicado_siscoaf") or "97"))

    if occurrence_codes:
        codig_el = _sub(sisc_el, "CodigosOcorrencia")
        for code in occurrence_codes:
            _sub(codig_el, "Codigo", str(code))

    _sub(sisc_el, "ValorPremio", f"{valor_premio:.2f}")
    _sub(sisc_el, "ValorApostas", f"{valor_apostas:.2f}")
    if info_adicionais:
        _sub(sisc_el, "InformacoesAdicionais", info_adicionais[:2000])

    # ── Narrativa do analista ─────────────────────────────────────────────────
    narrative = str(payload.get("analystNarrative") or payload.get("analyst_narrative") or "")
    if narrative:
        _sub(com, "NarrativaAnalista", narrative[:4000])

    # ── Serialize ─────────────────────────────────────────────────────────────
    ET.indent(root, space="  ")
    decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return decl + ET.tostring(root, encoding="unicode", xml_declaration=False)
