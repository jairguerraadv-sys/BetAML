"""RLS hardening for users and system_flags.

Revision ID: 20260522_000006
Revises: 20260519_000005
Create Date: 2026-05-22 00:06:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260522_000006"
down_revision = "20260519_000005"
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
                        FOR INSERT
                        WITH CHECK (tenant_id = current_tenant_id())
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
                        FOR DELETE
                        USING (tenant_id = current_tenant_id())
                $pol$;
            END IF;
        END
        $$;
        """
    ))

    conn.execute(sa.text(
        """
        DO $$
        BEGIN
            IF to_regclass('public.system_flags') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE system_flags ENABLE ROW LEVEL SECURITY';
                EXECUTE 'ALTER TABLE system_flags FORCE ROW LEVEL SECURITY';

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_system_flags_select ON system_flags';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_system_flags_select ON system_flags
                        FOR SELECT
                        USING (tenant_id = current_tenant_id())
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_system_flags_insert ON system_flags';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_system_flags_insert ON system_flags
                        FOR INSERT
                        WITH CHECK (tenant_id = current_tenant_id())
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_system_flags_update ON system_flags';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_system_flags_update ON system_flags
                        FOR UPDATE
                        USING (tenant_id = current_tenant_id())
                        WITH CHECK (tenant_id = current_tenant_id())
                $pol$;

                EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_system_flags_delete ON system_flags';
                EXECUTE $pol$
                    CREATE POLICY tenant_isolation_system_flags_delete ON system_flags
                        FOR DELETE
                        USING (tenant_id = current_tenant_id())
                $pol$;
            END IF;
        END
        $$;
        """
    ))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_delete ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_update ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_insert ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_select ON system_flags"))

    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_users_delete ON users"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_users_update ON users"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_users_insert ON users"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_users_select ON users"))
