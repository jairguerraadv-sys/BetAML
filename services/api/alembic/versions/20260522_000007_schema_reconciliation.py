"""Reconcile legacy Docker SQL schema with ORM/Alembic head.

Revision ID: 20260522_000007
Revises: 20260522_000006
Create Date: 2026-05-22 00:07:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260522_000007"
down_revision = "20260522_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    conn.execute(sa.text(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
        $$ LANGUAGE SQL STABLE;
        """
    ))

    # Older Docker-created databases have system_flags(key, value, ...). The
    # ORM and current API expect tenant-scoped flags. Rebuild the table only
    # when that legacy shape is detected; otherwise just make sure constraints
    # and indexes exist.
    conn.execute(sa.text(
        """
        DO $$
        BEGIN
            IF to_regclass('public.system_flags') IS NOT NULL
               AND EXISTS (
                   SELECT 1
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'system_flags'
                     AND column_name = 'key'
               )
               AND NOT EXISTS (
                   SELECT 1
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'system_flags'
                     AND column_name = 'tenant_id'
               )
            THEN
                ALTER TABLE system_flags DISABLE ROW LEVEL SECURITY;
                ALTER TABLE system_flags RENAME TO system_flags_legacy_20260522;

                CREATE TABLE system_flags (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    flag_name TEXT NOT NULL,
                    flag_value JSONB NOT NULL DEFAULT 'false'::jsonb,
                    updated_by UUID REFERENCES users(id),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (tenant_id, flag_name)
                );

                INSERT INTO system_flags (
                    tenant_id, flag_name, flag_value, updated_by, updated_at, created_at
                )
                SELECT
                    t.id,
                    l.key,
                    COALESCE(l.value, 'false'::jsonb),
                    l.updated_by,
                    COALESCE(l.updated_at, NOW()),
                    COALESCE(l.updated_at, NOW())
                FROM tenants t
                CROSS JOIN system_flags_legacy_20260522 l
                ON CONFLICT (tenant_id, flag_name) DO NOTHING;

                DROP TABLE system_flags_legacy_20260522;
            ELSIF to_regclass('public.system_flags') IS NULL THEN
                CREATE TABLE system_flags (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    flag_name TEXT NOT NULL,
                    flag_value JSONB NOT NULL DEFAULT 'false'::jsonb,
                    updated_by UUID REFERENCES users(id),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (tenant_id, flag_name)
                );
            ELSE
                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();
                UPDATE system_flags SET id = gen_random_uuid() WHERE id IS NULL;
                ALTER TABLE system_flags ALTER COLUMN id SET NOT NULL;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'system_flags'::regclass
                      AND contype = 'p'
                ) THEN
                    ALTER TABLE system_flags ADD PRIMARY KEY (id);
                END IF;

                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS tenant_id UUID;
                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS flag_name TEXT;
                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS flag_value JSONB DEFAULT 'false'::jsonb;
                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE system_flags ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

                ALTER TABLE system_flags ALTER COLUMN tenant_id SET NOT NULL;
                ALTER TABLE system_flags ALTER COLUMN flag_name SET NOT NULL;
                ALTER TABLE system_flags ALTER COLUMN flag_value SET NOT NULL;
            END IF;
        END
        $$;
        """
    ))

    for statement in (
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_system_flags_tenant_flag_name
            ON system_flags (tenant_id, flag_name)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_system_flags_tenant
            ON system_flags (tenant_id)
        """,
    ):
        conn.execute(sa.text(statement))

    # Docker migration_v16 created only scored_at; ORM and analytics read both
    # scored_at and created_at. Keep scored_at for compatibility and add
    # created_at as the canonical analytics timestamp.
    conn.execute(sa.text(
        """
        DO $$
        BEGIN
            IF to_regclass('public.model_inference_logs') IS NOT NULL THEN
                ALTER TABLE model_inference_logs
                    ADD COLUMN IF NOT EXISTS scored_at TIMESTAMPTZ DEFAULT NOW();
                ALTER TABLE model_inference_logs
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
                UPDATE model_inference_logs
                   SET created_at = COALESCE(created_at, scored_at, NOW())
                 WHERE created_at IS NULL;
                ALTER TABLE model_inference_logs
                    ALTER COLUMN created_at SET NOT NULL,
                    ALTER COLUMN created_at SET DEFAULT NOW();
                ALTER TABLE model_inference_logs
                    ALTER COLUMN scored_at SET DEFAULT NOW();
            END IF;
        END
        $$;
        """
    ))
    for statement in (
        """
        CREATE INDEX IF NOT EXISTS idx_model_inference_logs_tenant_created_at
            ON model_inference_logs (tenant_id, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_model_inference_logs_model_created_at
            ON model_inference_logs (model_id, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_model_inference_logs_tenant_player_created
            ON model_inference_logs (tenant_id, player_id, created_at DESC)
        """,
    ):
        conn.execute(sa.text(statement))

    # Notifications may contain both legacy and current columns. Backfill the
    # current ORM columns and keep legacy columns for old reports until removal.
    conn.execute(sa.text(
        """
        DO $$
        BEGIN
            IF to_regclass('public.notifications') IS NOT NULL THEN
                ALTER TABLE notifications ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE notifications ADD COLUMN IF NOT EXISTS reference_type TEXT;
                ALTER TABLE notifications ADD COLUMN IF NOT EXISTS reference_id TEXT;

                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'notifications'
                      AND column_name = 'read'
                ) THEN
                    UPDATE notifications SET is_read = COALESCE(is_read, read, FALSE);
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'notifications'
                      AND column_name = 'entity_type'
                ) THEN
                    UPDATE notifications SET reference_type = COALESCE(reference_type, entity_type);
                END IF;
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'notifications'
                      AND column_name = 'entity_id'
                ) THEN
                    UPDATE notifications SET reference_id = COALESCE(reference_id, entity_id);
                END IF;
            END IF;
        END
        $$;
        """
    ))

    for statement in (
        "ALTER TABLE system_flags ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE system_flags FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS tenant_isolation_system_flags_select ON system_flags",
        """
        CREATE POLICY tenant_isolation_system_flags_select ON system_flags
            FOR SELECT
            USING (tenant_id = current_tenant_id())
        """,
        "DROP POLICY IF EXISTS tenant_isolation_system_flags_insert ON system_flags",
        """
        CREATE POLICY tenant_isolation_system_flags_insert ON system_flags
            FOR INSERT
            WITH CHECK (tenant_id = current_tenant_id())
        """,
        "DROP POLICY IF EXISTS tenant_isolation_system_flags_update ON system_flags",
        """
        CREATE POLICY tenant_isolation_system_flags_update ON system_flags
            FOR UPDATE
            USING (tenant_id = current_tenant_id())
            WITH CHECK (tenant_id = current_tenant_id())
        """,
        "DROP POLICY IF EXISTS tenant_isolation_system_flags_delete ON system_flags",
        """
        CREATE POLICY tenant_isolation_system_flags_delete ON system_flags
            FOR DELETE
            USING (tenant_id = current_tenant_id())
        """,
    ):
        conn.execute(sa.text(statement))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_delete ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_update ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_insert ON system_flags"))
    conn.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_system_flags_select ON system_flags"))
    conn.execute(sa.text("DROP INDEX IF EXISTS idx_system_flags_tenant"))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_system_flags_tenant_flag_name"))
