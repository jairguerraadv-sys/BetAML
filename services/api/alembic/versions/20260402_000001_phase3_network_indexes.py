"""phase3: índices de rede para device_events, financial_transactions

Revision ID: 20260402_000001
Revises: 20260320_000001
Create Date: 2026-04-02 00:00:01

Adiciona índices parciais para as queries de grafo de rede do player
(GET /players/{id}/network):
  - device_events.device_hash — lookup por dispositivo compartilhado
  - device_events.ip_hash     — lookup por IP compartilhado
  - financial_transactions.bank_account_hash — lookup por conta bancária compartilhada
  - financial_transactions.payment_instrument — lookup por instrumento de pagamento

Todos os índices são de cobertura (include tenant_id + player_id) para
evitar table-scans adicionais e filtrados por IS NOT NULL para omitir
registros sem dado (particionamento implícito).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260402_000001"
down_revision = "20260320_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── device_events ─────────────────────────────────────────────────────────
    op.create_index(
        "idx_device_events_device_hash",
        "device_events",
        ["tenant_id", "device_hash", "player_id"],
        postgresql_where=sa.text("device_hash IS NOT NULL"),
    )
    op.create_index(
        "idx_device_events_ip_hash",
        "device_events",
        ["tenant_id", "ip_hash", "player_id"],
        postgresql_where=sa.text("ip_hash IS NOT NULL"),
    )

    # ── financial_transactions ────────────────────────────────────────────────
    op.create_index(
        "idx_financial_transactions_bank_account_hash",
        "financial_transactions",
        ["tenant_id", "bank_account_hash", "player_id"],
        postgresql_where=sa.text("bank_account_hash IS NOT NULL"),
    )
    op.create_index(
        "idx_financial_transactions_payment_instrument",
        "financial_transactions",
        ["tenant_id", "payment_instrument", "player_id"],
        postgresql_where=sa.text("payment_instrument IS NOT NULL"),
    )

    # ── feature_snapshots — suporte ao data quality dashboard ─────────────────
    op.create_index(
        "idx_feature_snapshots_tenant_created",
        "feature_snapshots",
        ["tenant_id", "created_at"],
    )

    # ── ingest_errors — suporte ao data quality dashboard ────────────────────
    op.create_index(
        "idx_ingest_errors_tenant_created",
        "ingest_errors",
        ["tenant_id", "created_at"],
    )

    # ── report_packages — suporte ao COAF funnel (pld-kpis) ──────────────────
    op.create_index(
        "idx_report_packages_tenant_status_created",
        "report_packages",
        ["tenant_id", "status", "created_at"],
    )

    # ── alerts — suporte à precision (label + created_at) ────────────────────
    op.create_index(
        "idx_alerts_tenant_label_created",
        "alerts",
        ["tenant_id", "label", "created_at"],
        postgresql_where=sa.text("label IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_alerts_tenant_label_created", table_name="alerts")
    op.drop_index("idx_report_packages_tenant_status_created", table_name="report_packages")
    op.drop_index("idx_ingest_errors_tenant_created", table_name="ingest_errors")
    op.drop_index("idx_feature_snapshots_tenant_created", table_name="feature_snapshots")
    op.drop_index("idx_financial_transactions_payment_instrument", table_name="financial_transactions")
    op.drop_index("idx_financial_transactions_bank_account_hash", table_name="financial_transactions")
    op.drop_index("idx_device_events_ip_hash", table_name="device_events")
    op.drop_index("idx_device_events_device_hash", table_name="device_events")
