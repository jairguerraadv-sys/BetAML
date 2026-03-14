"""v13: tenant cnpj + audit_logs pii_accessed

Revision ID: 20260314_000004
Revises: 20260314_000003
Create Date: 2026-03-14 00:00:04

Corresponds to: infra/migration_v13.sql
GAP-9:  Add cnpj column to tenants (required for COAF RIF CnpjComunicante field).
GAP-10: Add pii_accessed column to audit_logs (LGPD Art. 37 — queryable PII trail).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260314_000004"
down_revision = "20260314_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GAP-9: CNPJ do operador para geração de XML COAF (MIFD v3)
    op.add_column(
        "tenants",
        sa.Column(
            "cnpj",
            sa.String(14),
            nullable=True,
            comment="CNPJ do operador (14 dígitos sem pontuação) — obrigatório para XML COAF MIFD v3",
        ),
    )

    # GAP-10: Campo de PII acessado para relatórios LGPD Art. 37
    op.add_column(
        "audit_logs",
        sa.Column(
            "pii_accessed",
            sa.Text(),
            nullable=True,
            comment="Campo PII acessado — cpf, cpf_masked, full_name, etc. (LGPD Art. 37)",
        ),
    )
    op.create_index(
        "idx_audit_logs_pii_accessed",
        "audit_logs",
        ["tenant_id", "pii_accessed"],
        postgresql_where=sa.text("pii_accessed IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_audit_logs_pii_accessed", table_name="audit_logs")
    op.drop_column("audit_logs", "pii_accessed")
    op.drop_column("tenants", "cnpj")
