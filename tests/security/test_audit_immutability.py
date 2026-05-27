"""Database-first immutability tests for audit_logs (PR-08).

Validates that audit logs remain append-only at the PostgreSQL layer:
INSERT is allowed (with tenant context), UPDATE/DELETE are blocked by trigger,
and tenant isolation remains enforced by RLS.
"""
from __future__ import annotations

import os
import uuid

import pytest


POSTGRES_DSN = os.getenv(
    "BETAML_TEST_DB_URL",
    "postgresql://betaml:devpass@localhost:5432/betaml_dev",
)
RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack nao disponivel. Use TEST_STACK_UP=1 para rodar testes de banco.",
)


async def _connect():
    import asyncpg  # noqa: PLC0415

    return await asyncpg.connect(POSTGRES_DSN)


async def _set_tenant(conn, tenant_id: str | None) -> None:
    if tenant_id:
        await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant_id)
    else:
        await conn.execute("SELECT set_config('app.current_tenant', '', false)")


@pytest.fixture(scope="module")
async def pg():
    if not RUN_INTEGRATION:
        pytest.skip("Stack nao disponivel")
    conn = await _connect()
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def requires_non_bypass_role(pg):
    is_bypass = await pg.fetchval(
        """
        SELECT r.rolbypassrls
        FROM pg_roles r
        WHERE r.rolname = current_user
        """
    )
    if bool(is_bypass):
        pytest.skip(
            "Role de conexao possui BYPASSRLS; testes runtime de isolamento nao sao validos."
        )
    return True


@pytest.fixture(scope="module")
async def tenants_and_users(pg, requires_non_bypass_role):
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    user_admin = str(uuid.uuid4())
    user_regular = str(uuid.uuid4())

    await _set_tenant(pg, None)
    await pg.execute(
        "INSERT INTO tenants (id, name, slug, active, settings, risk_score_threshold, plan_tier) "
        "VALUES ($1, $2, $3, true, '{}'::jsonb, 0.75, 'standard') ON CONFLICT (id) DO NOTHING",
        tenant_a,
        f"Audit Immutability A {tenant_a[:8]}",
        f"audit-immut-a-{tenant_a[:8]}",
    )
    await pg.execute(
        "INSERT INTO tenants (id, name, slug, active, settings, risk_score_threshold, plan_tier) "
        "VALUES ($1, $2, $3, true, '{}'::jsonb, 0.75, 'standard') ON CONFLICT (id) DO NOTHING",
        tenant_b,
        f"Audit Immutability B {tenant_b[:8]}",
        f"audit-immut-b-{tenant_b[:8]}",
    )

    await _set_tenant(pg, tenant_a)
    await pg.execute(
        """
        INSERT INTO users (id, tenant_id, username, email, password_hash, role, active)
        VALUES ($1, $2, $3, $4, $5, $6, true)
        ON CONFLICT (id) DO NOTHING
        """,
        user_admin,
        tenant_a,
        f"audit_admin_{tenant_a[:8]}",
        f"audit_admin_{tenant_a[:8]}@example.test",
        "hash",
        "SUPER_ADMIN",
    )
    await pg.execute(
        """
        INSERT INTO users (id, tenant_id, username, email, password_hash, role, active)
        VALUES ($1, $2, $3, $4, $5, $6, true)
        ON CONFLICT (id) DO NOTHING
        """,
        user_regular,
        tenant_a,
        f"audit_user_{tenant_a[:8]}",
        f"audit_user_{tenant_a[:8]}@example.test",
        "hash",
        "AML_ANALYST",
    )

    yield {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_admin": user_admin,
        "user_regular": user_regular,
    }


async def _insert_audit_row(conn, tenant_id: str, user_id: str, action: str) -> str:
    await _set_tenant(conn, tenant_id)
    row_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO audit_logs (id, tenant_id, user_id, action, entity_type, entity_id)
        VALUES ($1, $2, $3, $4, 'SecurityTest', $5)
        """,
        row_id,
        tenant_id,
        user_id,
        action,
        f"entity-{row_id[:8]}",
    )
    return row_id


@pytest.mark.asyncio
@skip_unless_stack
async def test_trigger_exists_in_catalog(pg):
    row = await pg.fetchrow(
        """
        SELECT t.tgname, p.proname
        FROM pg_trigger t
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE t.tgrelid = 'audit_logs'::regclass
          AND NOT t.tgisinternal
          AND t.tgname = 'trg_prevent_audit_logs_mutation'
        """
    )
    assert row is not None
    assert row["proname"] == "prevent_audit_logs_mutation"


@pytest.mark.asyncio
@skip_unless_stack
async def test_insert_is_allowed(pg, tenants_and_users):
    row_id = await _insert_audit_row(
        pg,
        tenants_and_users["tenant_a"],
        tenants_and_users["user_regular"],
        "PR08_INSERT_ALLOWED",
    )
    found = await pg.fetchval(
        "SELECT count(*) FROM audit_logs WHERE id = $1",
        row_id,
    )
    assert found == 1


@pytest.mark.asyncio
@skip_unless_stack
async def test_update_is_blocked_by_trigger(pg, tenants_and_users):
    row_id = await _insert_audit_row(
        pg,
        tenants_and_users["tenant_a"],
        tenants_and_users["user_regular"],
        "PR08_UPDATE_BLOCKED",
    )
    with pytest.raises(Exception) as exc:
        await pg.execute(
            "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = $1",
            row_id,
        )
    assert "immutable" in str(exc.value).lower()


@pytest.mark.asyncio
@skip_unless_stack
async def test_delete_is_blocked_by_trigger(pg, tenants_and_users):
    row_id = await _insert_audit_row(
        pg,
        tenants_and_users["tenant_a"],
        tenants_and_users["user_regular"],
        "PR08_DELETE_BLOCKED",
    )
    with pytest.raises(Exception) as exc:
        await pg.execute("DELETE FROM audit_logs WHERE id = $1", row_id)
    assert "immutable" in str(exc.value).lower()


@pytest.mark.asyncio
@skip_unless_stack
async def test_super_admin_cannot_mutate_audit_logs(pg, tenants_and_users):
    row_id = await _insert_audit_row(
        pg,
        tenants_and_users["tenant_a"],
        tenants_and_users["user_admin"],
        "PR08_SUPER_ADMIN_BLOCKED",
    )

    with pytest.raises(Exception) as update_exc:
        await pg.execute(
            "UPDATE audit_logs SET action = 'SUPER_ADMIN_TAMPER' WHERE id = $1",
            row_id,
        )
    assert "immutable" in str(update_exc.value).lower()

    with pytest.raises(Exception) as delete_exc:
        await pg.execute("DELETE FROM audit_logs WHERE id = $1", row_id)
    assert "immutable" in str(delete_exc.value).lower()


@pytest.mark.asyncio
@skip_unless_stack
async def test_cross_tenant_cannot_read_other_tenant_log(pg, tenants_and_users):
    row_id = await _insert_audit_row(
        pg,
        tenants_and_users["tenant_a"],
        tenants_and_users["user_regular"],
        "PR08_CROSS_TENANT",
    )

    await _set_tenant(pg, tenants_and_users["tenant_b"])
    count = await pg.fetchval("SELECT count(*) FROM audit_logs WHERE id = $1", row_id)
    assert count == 0


@pytest.mark.asyncio
@skip_unless_stack
async def test_without_tenant_context_cannot_insert(pg, tenants_and_users):
    await _set_tenant(pg, None)
    visible = await pg.fetchval("SELECT count(*) FROM audit_logs")
    assert visible == 0

    with pytest.raises(Exception):
        await pg.execute(
            """
            INSERT INTO audit_logs (id, tenant_id, user_id, action, entity_type, entity_id)
            VALUES ($1, $2, $3, 'PR08_NO_TENANT', 'SecurityTest', 'entity-no-tenant')
            """,
            str(uuid.uuid4()),
            tenants_and_users["tenant_a"],
            tenants_and_users["user_regular"],
        )
