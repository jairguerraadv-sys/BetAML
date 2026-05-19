"""Phase 2 — PLD Completo: colunas de cadeia de custódia XML e protocolo COAF
em report_packages.

Revision ID: 20260519_000004
Revises: 20260519_000003
Create Date: 2026-05-19 00:04:00.000000

Refs regulatórios:
  - COAF Res. 36/2021 Art. 9º  — prazo 30 dias, número de protocolo
  - Portaria SPA/MF 1.143/2024 — cadeia de custódia do XML Siscoaf v2.1
  - Lei 14.790/2023 Art. 33     — self_exclusion e deposit_limit já na tabela
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision      = "20260519_000004"
down_revision = "20260519_000003"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── report_packages: cadeia de custódia XML e protocolo COAF ────────────
    conn.execute(sa.text("""
        ALTER TABLE report_packages
            ADD COLUMN IF NOT EXISTS xml_path             TEXT,
            ADD COLUMN IF NOT EXISTS xml_sha256           VARCHAR(64),
            ADD COLUMN IF NOT EXISTS coaf_protocol_number VARCHAR(80),
            ADD COLUMN IF NOT EXISTS filed_at             TIMESTAMPTZ;
    """))

    # Índice para busca eficiente de ReportPackages não-FILED com decision FILE_SAR
    # (usado pelo job check_coaf_reporting_deadlines)
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_rp_unfiled_decision
            ON report_packages (tenant_id, status, decision, created_at)
            WHERE status <> 'FILED';
    """))

    # Índice para busca por protocolo COAF (auditoria regulatória)
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_rp_coaf_protocol
            ON report_packages (tenant_id, coaf_protocol_number)
            WHERE coaf_protocol_number IS NOT NULL;
    """))

    # ── Backfill: preenche filed_at para ReportPackages já FILED ────────────
    # Usa closed_at do caso correspondente como proxy (melhor estimativa).
    conn.execute(sa.text("""
        UPDATE report_packages rp
           SET filed_at = c.closed_at
          FROM cases c
         WHERE rp.case_id = c.id
           AND rp.status = 'FILED'
           AND rp.filed_at IS NULL
           AND c.closed_at IS NOT NULL;
    """))

    # Para registros FILED sem closed_at no caso, usa o created_at do próprio RP
    conn.execute(sa.text("""
        UPDATE report_packages
           SET filed_at = created_at
         WHERE status = 'FILED'
           AND filed_at IS NULL;
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("DROP INDEX IF EXISTS idx_rp_coaf_protocol"))
    conn.execute(sa.text("DROP INDEX IF EXISTS idx_rp_unfiled_decision"))

    conn.execute(sa.text("""
        ALTER TABLE report_packages
            DROP COLUMN IF EXISTS filed_at,
            DROP COLUMN IF EXISTS coaf_protocol_number,
            DROP COLUMN IF EXISTS xml_sha256,
            DROP COLUMN IF EXISTS xml_path;
    """))
