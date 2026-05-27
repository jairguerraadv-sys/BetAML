"""Runtime RLS hardening for production-critical tenant tables.

Revision ID: 20260526_000001
Revises: 20260525_000001
Create Date: 2026-05-26 00:01:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260526_000001"
down_revision = "20260525_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
        $$ LANGUAGE SQL STABLE;
        """
    ))

    conn.execute(sa.text(
        """
        DO $$
        BEGIN
            IF to_regclass('public.users') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE users ENABLE ROW LEVEL SECURITY';
                EXECUTE 'ALTER TABLE users FORCE ROW LEVEL SECURITY';

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_users_select ON users';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_users_select ON users
                        FOR SELECT
                        USING (
                            tenant_id = current_tenant_id()
                            OR current_setting('app.auth_flow', TRUE) IN ('login', 'refresh')
                        )
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_users_insert ON users';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_users_insert ON users
                        FOR INSERT WITH CHECK (tenant_id = current_tenant_id())
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_users_update ON users';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_users_update ON users
                        FOR UPDATE
                        USING (tenant_id = current_tenant_id())
                        WITH CHECK (tenant_id = current_tenant_id())
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_users_delete ON users';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_users_delete ON users
                        FOR DELETE USING (tenant_id = current_tenant_id())
                $pol$;
            END IF;
        END
        $$;
        """
    ))

    for table_name in ("financial_transactions", "bets", "model_registry"):
        conn.execute(sa.text(f"""
            DO $$
            BEGIN
                IF to_regclass('public.{table_name}') IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY';
                    EXECUTE 'ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY';
                    EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}';
                    EXECUTE 'CREATE POLICY tenant_isolation_{table_name} ON {table_name}
                             USING (tenant_id = current_tenant_id())
                             WITH CHECK (tenant_id = current_tenant_id())';
                END IF;
            END
            $$;
        """))


def downgrade() -> None:
    conn = op.get_bind()
    for table_name in ("financial_transactions", "bets", "model_registry"):
        conn.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"))
        conn.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY"))
