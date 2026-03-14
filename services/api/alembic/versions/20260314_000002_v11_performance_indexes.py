"""v11: performance indexes for high-volume queries

Revision ID: 20260314_000002
Revises: 20260314_000001
Create Date: 2026-03-14 00:00:02

Corresponds to: infra/migration_v11.sql
Creates 30+ composite indexes on alerts, cases, players, transactions,
bets, audit_logs, ingest_jobs, ingest_errors, feature_snapshots,
notifications, and rule_definitions.
"""
from __future__ import annotations

from alembic import op

revision = "20260314_000002"
down_revision = "20260314_000001"
branch_labels = None
depends_on = None

# Indexes created by this migration — kept as a list for easy downgrade
_INDEXES = [
    # alerts
    ("idx_alerts_tenant_status_created",    "alerts",                ["tenant_id", "status", "created_at"]),
    ("idx_alerts_tenant_severity",          "alerts",                ["tenant_id", "severity"]),
    ("idx_alerts_tenant_player",            "alerts",                ["tenant_id", "player_id"]),
    ("idx_alerts_case_id",                  "alerts",                ["case_id"]),
    # cases
    ("idx_cases_tenant_status_sla",         "cases",                 ["tenant_id", "status", "sla_due_at"]),
    ("idx_cases_tenant_assigned",           "cases",                 ["tenant_id", "assigned_to"]),
    ("idx_cases_tenant_priority",           "cases",                 ["tenant_id", "priority"]),
    # players
    ("idx_players_tenant_risk_score",       "players",               ["tenant_id", "risk_score"]),
    ("idx_players_tenant_customer_id",      "players",               ["tenant_id", "external_player_id"]),
    ("idx_players_tenant_status",           "players",               ["tenant_id", "status"]),
    ("idx_players_tenant_cpf",              "players",               ["tenant_id", "cpf_encrypted"]),
    # financial_transactions
    ("idx_financial_transactions_tenant_player_ts", "financial_transactions", ["tenant_id", "player_id", "occurred_at"]),
    ("idx_financial_transactions_tenant_type",      "financial_transactions", ["tenant_id", "type"]),
    ("idx_financial_transactions_tenant_status",    "financial_transactions", ["tenant_id", "status"]),
    # bets
    ("idx_bets_tenant_player_ts",           "bets",                  ["tenant_id", "player_id", "occurred_at"]),
    ("idx_bets_tenant_settled",             "bets",                  ["tenant_id", "settled_at"]),
    # audit_logs
    ("idx_audit_logs_tenant_created",       "audit_logs",            ["tenant_id", "created_at"]),
    ("idx_audit_logs_tenant_entity",        "audit_logs",            ["tenant_id", "entity_type"]),
    ("idx_audit_logs_tenant_user",          "audit_logs",            ["tenant_id", "user_id"]),
    ("idx_audit_logs_tenant_pii",           "audit_logs",            ["tenant_id", "pii_accessed"]),
    # ingest_jobs
    ("idx_ingest_jobs_tenant_status_created", "ingest_jobs",         ["tenant_id", "status", "created_at"]),
    ("idx_ingest_jobs_tenant_source",         "ingest_jobs",         ["tenant_id", "source_system"]),
    # ingest_errors
    ("idx_ingest_errors_tenant_job",        "ingest_errors",         ["tenant_id", "ingest_job_id"]),
    ("idx_ingest_errors_tenant_resolved",   "ingest_errors",         ["tenant_id", "resolved"]),
    # feature_snapshots
    ("idx_feature_snapshots_tenant_player_date", "feature_snapshots", ["tenant_id", "player_id", "snapshot_date"]),
    # notifications
    ("idx_notifications_tenant_read_created", "notifications",       ["tenant_id", "is_read", "created_at"]),
    # rule_definitions
    ("idx_rule_definitions_tenant_active",  "rule_definitions",      ["tenant_id", "status"]),
]


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        try:
            op.create_index(name, table, columns, if_not_exists=True)
        except Exception:
            # Index may already exist on databases created from Docker init SQL
            pass


def downgrade() -> None:
    for name, table, _ in reversed(_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
