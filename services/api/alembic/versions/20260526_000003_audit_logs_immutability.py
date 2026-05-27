"""Dedicated audit_logs immutability hardening (PR-08).

Revision ID: 20260526_000003
Revises: 20260526_000002
Create Date: 2026-05-26 00:03:00

This migration enforces append-only semantics for audit_logs at the database
layer with a dedicated trigger that blocks UPDATE and DELETE regardless of
application role claims (including SUPER_ADMIN in app-level RBAC).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260526_000003"
down_revision = "20260526_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_audit_logs_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is immutable: operation % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """))

    # Drop legacy and canonical trigger names to keep upgrade idempotent.
    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_prevent_audit_log_update_delete ON audit_logs;
    """))
    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_prevent_audit_logs_mutation ON audit_logs;
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER trg_prevent_audit_logs_mutation
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_logs_mutation();
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_prevent_audit_logs_mutation ON audit_logs;
    """))

    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is immutable: operation % is not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """))

    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_prevent_audit_log_update_delete ON audit_logs;
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER trg_prevent_audit_log_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
    """))
