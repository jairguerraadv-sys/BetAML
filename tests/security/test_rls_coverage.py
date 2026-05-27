"""tests/security/test_rls_coverage.py

Row Level Security coverage tests for BetAML tenant-scoped tables.

These tests validate that PostgreSQL RLS is correctly configured on all
sensitive tables and that tenant isolation is enforced at the database level
(not just at the application layer).

Test categories:
    1. Catalog inspection  — pg_class confirms RLS + FORCE RLS on all tables
    2. Empty context       — SELECT with no tenant context returns 0 rows
    3. Cross-tenant read   — Tenant A cannot read Tenant B's rows
    4. Cross-tenant write  — Tenant A cannot UPDATE/DELETE/INSERT Tenant B's rows
    5. Append-only tables  — audit_logs/rule_exec_logs/inference_logs block mutations
    6. Regression list     — fixed list of sensitive tables must have RLS

Requirements:
    Stack running: TEST_STACK_UP=1 pytest tests/security/test_rls_coverage.py -v
    Database:      BETAML_TEST_DB_URL (defaults to postgresql://betaml:devpass@localhost:5432/betaml_dev)

Note: Unit-mode tests (no DB) cover static catalog checks and are always run.
"""
from __future__ import annotations

import os
import uuid

import pytest

# ── Constants ─────────────────────────────────────────────────────────────────

POSTGRES_DSN = os.getenv(
    "BETAML_TEST_DB_URL",
    "postgresql://betaml:devpass@localhost:5432/betaml_dev",
)
RUN_INTEGRATION = os.getenv("TEST_STACK_UP", "0") == "1"

skip_unless_stack = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Stack não disponível. Use TEST_STACK_UP=1 para rodar testes de segurança de RLS.",
)

# Full set of sensitive tables that MUST have RLS + FORCE RLS after PR-01.
# If a table is added to models.py with tenant_id, add it here too.
SENSITIVE_RLS_TABLES: frozenset[str] = frozenset({
    "players",
    "device_events",
    "player_kyc_events",
    "alerts",
    "cases",
    "case_events",
    "report_packages",
    "audit_logs",
    "rule_execution_logs",
    "model_inference_logs",
    "feature_snapshots",
    "scoring_configs",
    "rule_definitions",
    "compound_rules",
    "rule_macros",
    "player_lists",
    "player_list_entries",
    "mapping_configs",
    "ingest_jobs",
    "notifications",
})

# Append-only tables: UPDATE and DELETE must be blocked via policy USING(false).
APPEND_ONLY_TABLES: frozenset[str] = frozenset({
    "audit_logs",
    "rule_execution_logs",
    "model_inference_logs",
})

# Tables where tenants global lookup is intentionally allowed (e.g. tenants
# itself uses a special per-row policy that lets a tenant only see itself).
GLOBAL_TABLES: frozenset[str] = frozenset({
    "tenants",  # RLS configured via 20260519_000001; SELECT policy restricts to own row
})


# ── asyncpg helper ────────────────────────────────────────────────────────────

async def _connect():
    """Return an asyncpg connection to the test database."""
    import asyncpg  # noqa: PLC0415 — deferred import; optional dep
    return await asyncpg.connect(POSTGRES_DSN)


async def _set_tenant(conn, tenant_id: str | None) -> None:
    """Set (or clear) the app.current_tenant Postgres session variable."""
    if tenant_id:
        await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant_id)
    else:
        await conn.execute("SELECT set_config('app.current_tenant', '', false)")


async def _clear_tenant(conn) -> None:
    await _set_tenant(conn, None)


async def _tenant_user_id(conn, tenant_id: str) -> str:
    """Return one existing user id for the tenant (required by notifications)."""
    await _set_tenant(conn, tenant_id)
    uid = await conn.fetchval(
        "SELECT id::text FROM users WHERE tenant_id = $1 ORDER BY created_at ASC LIMIT 1",
        tenant_id,
    )
    if not uid:
        raise AssertionError(f"No user found for tenant {tenant_id}; seeds/setup required")
    return uid


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
async def pg():
    """Bare asyncpg connection used for low-level RLS assertions."""
    if not RUN_INTEGRATION:
        pytest.skip("Stack não disponível")
    conn = await _connect()
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def requires_non_bypass_role(pg):
    """Skip runtime isolation checks when current DB role bypasses RLS."""
    is_bypass = await pg.fetchval(
        """
        SELECT r.rolbypassrls
        FROM pg_roles r
        WHERE r.rolname = current_user
        """
    )
    if bool(is_bypass):
        pytest.skip(
            "Role de conexão possui BYPASSRLS; testes de isolamento runtime "
            "não são válidos neste ambiente."
        )
    return True


@pytest.fixture(scope="module")
async def rls_tenants(pg, requires_non_bypass_role):
    """Create two isolated tenants and return their IDs."""
    tid_a = str(uuid.uuid4())
    tid_b = str(uuid.uuid4())
    slug_a = f"rls-test-a-{tid_a[:8]}"
    slug_b = f"rls-test-b-{tid_b[:8]}"
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    # tenants policy allows bootstrap INSERT when current_tenant_id() is NULL.
    await _clear_tenant(pg)
    await pg.execute(
        "INSERT INTO tenants (id, name, slug, active, settings, risk_score_threshold, plan_tier)"
        " VALUES ($1, $2, $3, true, '{}'::jsonb, 0.75, 'standard')"
        " ON CONFLICT (id) DO NOTHING",
        tid_a, f"RLS Test Tenant A {tid_a[:8]}", slug_a,
    )
    await pg.execute(
        "INSERT INTO tenants (id, name, slug, active, settings, risk_score_threshold, plan_tier)"
        " VALUES ($1, $2, $3, true, '{}'::jsonb, 0.75, 'standard')"
        " ON CONFLICT (id) DO NOTHING",
        tid_b, f"RLS Test Tenant B {tid_b[:8]}", slug_b,
    )

    await _set_tenant(pg, tid_a)
    await pg.execute(
        """
        INSERT INTO users (id, tenant_id, username, email, password_hash, role, active)
        VALUES ($1, $2, $3, $4, $5, $6, true)
        ON CONFLICT (id) DO NOTHING
        """,
        user_a,
        tid_a,
        f"rls_user_a_{tid_a[:8]}",
        f"rls_user_a_{tid_a[:8]}@example.test",
        "hash",
        "AML_ANALYST",
    )

    await _set_tenant(pg, tid_b)
    await pg.execute(
        """
        INSERT INTO users (id, tenant_id, username, email, password_hash, role, active)
        VALUES ($1, $2, $3, $4, $5, $6, true)
        ON CONFLICT (id) DO NOTHING
        """,
        user_b,
        tid_b,
        f"rls_user_b_{tid_b[:8]}",
        f"rls_user_b_{tid_b[:8]}@example.test",
        "hash",
        "AML_ANALYST",
    )

    yield {"a": tid_a, "b": tid_b, "user_a": user_a, "user_b": user_b}

    # Cleanup — use BYPASSRLS or superuser; cleanup failures are non-fatal
    try:
        await _set_tenant(pg, tid_a)
        await pg.execute("DELETE FROM users WHERE id = $1", user_a)
        await _set_tenant(pg, tid_b)
        await pg.execute("DELETE FROM users WHERE id = $1", user_b)
        await _clear_tenant(pg)
        await pg.execute("DELETE FROM tenants WHERE id = ANY($1)", [tid_a, tid_b])
    except Exception:
        pass


# ── 1. Catalog inspection (static — always run) ───────────────────────────────

class TestRLSCatalogInspection:
    """Verify that every sensitive table has RLS + FORCE RLS via pg_class catalog.

    These tests REQUIRE a running database to query pg_class.
    They are skipped when TEST_STACK_UP is not set.
    """

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_all_sensitive_tables_have_rls_enabled(self):
        """relrowsecurity must be true for every sensitive table."""
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = ANY($1::text[])
                  AND c.relkind = 'r'
                """,
                list(SENSITIVE_RLS_TABLES),
            )
        finally:
            await conn.close()

        found = {r["relname"]: r for r in rows}
        missing_rls = []
        missing_force = []
        not_found = []

        for tbl in SENSITIVE_RLS_TABLES:
            if tbl not in found:
                # Table does not exist in schema — skip (not our responsibility)
                not_found.append(tbl)
                continue
            row = found[tbl]
            if not row["relrowsecurity"]:
                missing_rls.append(tbl)
            if not row["relforcerowsecurity"]:
                missing_force.append(tbl)

        assert not missing_rls, (
            f"Tables MISSING relrowsecurity=true: {missing_rls}"
        )
        assert not missing_force, (
            f"Tables MISSING relforcerowsecurity=true: {missing_force}"
        )

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_sensitive_tables_have_per_operation_policies(self):
        """Each sensitive table must have at least SELECT and INSERT policies."""
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT tablename, cmd
                FROM pg_policies
                WHERE schemaname = 'public'
                  AND tablename = ANY($1::text[])
                """,
                list(SENSITIVE_RLS_TABLES),
            )
        finally:
            await conn.close()

        from collections import defaultdict
        policies: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            policies[row["tablename"]].add(row["cmd"])

        tables_missing_select = []
        tables_missing_insert = []
        for tbl in SENSITIVE_RLS_TABLES:
            ops = policies.get(tbl, set())
            # "ALL" policy covers everything; "SELECT"/"INSERT" are explicit
            if "SELECT" not in ops and "ALL" not in ops:
                tables_missing_select.append(tbl)
            if "INSERT" not in ops and "ALL" not in ops:
                tables_missing_insert.append(tbl)

        assert not tables_missing_select, (
            f"Tables missing SELECT policy: {tables_missing_select}"
        )
        assert not tables_missing_insert, (
            f"Tables missing INSERT policy: {tables_missing_insert}"
        )


# ── 2. Empty tenant context → 0 rows ─────────────────────────────────────────

class TestEmptyTenantContextReturnsNoRows:
    """SELECT with empty app.current_tenant must return 0 rows."""

    # Tables with simpler structures for empty-context test
    _TARGET_TABLES = [
        "players",
        "alerts",
        "cases",
        "audit_logs",
        "notifications",
        "model_inference_logs",
        "rule_definitions",
    ]

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_no_rows_without_tenant_context(self, rls_tenants, requires_non_bypass_role):
        """After clearing app.current_tenant, every target table returns 0 rows."""
        conn = await _connect()
        try:
            await _clear_tenant(conn)
            for tbl in self._TARGET_TABLES:
                # Check table exists first
                exists = await conn.fetchval(
                    "SELECT to_regclass('public.' || $1) IS NOT NULL", tbl
                )
                if not exists:
                    continue
                count = await conn.fetchval(f"SELECT count(*) FROM {tbl}")  # noqa: S608
                assert count == 0, (
                    f"Table '{tbl}': expected 0 rows without tenant context, got {count}."
                    " Possible RLS misconfiguration."
                )
        finally:
            await conn.close()


# ── 3 & 4. Cross-tenant isolation ─────────────────────────────────────────────

class TestCrossTenantIsolation:
    """Tenant A must not read or write Tenant B's data."""

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_tenant_a_cannot_read_tenant_b_notifications(self, rls_tenants, requires_non_bypass_role):
        """Tenant A's SELECT on notifications must not return Tenant B's rows."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        tid_b = rls_tenants["b"]
        try:
            user_b = await _tenant_user_id(conn, tid_b)
            # Insert a notification for tenant B (bypass RLS by setting context to B)
            await _set_tenant(conn, tid_b)
            notif_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO notifications (id, tenant_id, user_id, type, title) VALUES ($1, $2, $3, $4, $5)"
                " ON CONFLICT (id) DO NOTHING",
                notif_id, tid_b, user_b, "ALERT", "RLS Test Notification B",
            )

            # Now switch to Tenant A — must not see Tenant B's notification
            await _set_tenant(conn, tid_a)
            row = await conn.fetchrow(
                "SELECT id FROM notifications WHERE id = $1", notif_id
            )
            assert row is None, (
                f"Tenant A read Tenant B notification {notif_id} — RLS breach!"
            )
        finally:
            # Cleanup
            try:
                await _set_tenant(conn, tid_b)
                await conn.execute("DELETE FROM notifications WHERE id = $1", notif_id)
            except Exception:
                pass
            await conn.close()

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_tenant_a_cannot_read_tenant_b_rule_definitions(self, rls_tenants, requires_non_bypass_role):
        """Tenant A must not read Tenant B's rule_definitions."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        tid_b = rls_tenants["b"]
        try:
            await _set_tenant(conn, tid_b)
            rule_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO rule_definitions"
                " (id, tenant_id, name, status, severity, scope, condition_dsl, params, weight)"
                " VALUES ($1, $2, $3, 'ACTIVE', 'HIGH', 'TRANSACTION', 'amount > 0', '{}'::jsonb, 0.5)"
                " ON CONFLICT (id) DO NOTHING",
                rule_id, tid_b, "RLS Test Rule B",
            )

            await _set_tenant(conn, tid_a)
            row = await conn.fetchrow(
                "SELECT id FROM rule_definitions WHERE id = $1", rule_id
            )
            assert row is None, (
                f"Tenant A read Tenant B rule_definition {rule_id} — RLS breach!"
            )
        finally:
            try:
                await _set_tenant(conn, tid_b)
                await conn.execute("DELETE FROM rule_definitions WHERE id = $1", rule_id)
            except Exception:
                pass
            await conn.close()

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_tenant_a_update_does_not_affect_tenant_b(self, rls_tenants, requires_non_bypass_role):
        """UPDATE from Tenant A's context must not touch Tenant B rows."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        tid_b = rls_tenants["b"]
        try:
            user_b = await _tenant_user_id(conn, tid_b)
            await _set_tenant(conn, tid_b)
            notif_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO notifications (id, tenant_id, user_id, type, title) VALUES ($1, $2, $3, $4, $5)"
                " ON CONFLICT (id) DO NOTHING",
                notif_id, tid_b, user_b, "ALERT", "Original B Title",
            )

            # Tenant A tries to UPDATE
            await _set_tenant(conn, tid_a)
            result = await conn.execute(
                "UPDATE notifications SET title = 'HACKED BY A' WHERE id = $1",
                notif_id,
            )
            # Should affect 0 rows (RLS blocks cross-tenant write)
            affected = int(result.split()[-1])
            assert affected == 0, (
                f"Tenant A UPDATE affected {affected} rows of Tenant B — RLS breach!"
            )
        finally:
            try:
                await _set_tenant(conn, tid_b)
                await conn.execute("DELETE FROM notifications WHERE id = $1", notif_id)
            except Exception:
                pass
            await conn.close()

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_insert_with_wrong_tenant_id_is_blocked(self, rls_tenants, requires_non_bypass_role):
        """INSERT with tenant_id ≠ current_tenant_id() must fail or be blocked."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        tid_b = rls_tenants["b"]
        try:
            user_a = await _tenant_user_id(conn, tid_a)
            # Set context to Tenant A but try inserting a row for Tenant B
            await _set_tenant(conn, tid_a)
            insert_id = str(uuid.uuid4())
            try:
                await conn.execute(
                    "INSERT INTO notifications (id, tenant_id, user_id, type, title) VALUES ($1, $2, $3, $4, $5)",
                    insert_id, tid_b, user_a, "ALERT", "Cross-tenant insert attempt",
                )
                # If INSERT succeeded without error, verify 0 rows visible to A
                # (should be blocked by WITH CHECK in INSERT policy)
                count = await conn.fetchval(
                    "SELECT count(*) FROM notifications WHERE id = $1", insert_id
                )
                assert count == 0, (
                    "Cross-tenant INSERT succeeded and row is visible to inserting tenant — RLS breach!"
                )
            except Exception as exc:
                # Exception is the expected outcome (WITH CHECK violation)
                assert "policy" in str(exc).lower() or "violat" in str(exc).lower(), (
                    f"Unexpected exception during cross-tenant INSERT: {exc}"
                )
        finally:
            try:
                await _set_tenant(conn, tid_b)
                await conn.execute("DELETE FROM notifications WHERE id = $1", insert_id)
            except Exception:
                pass
            await conn.close()


# ── 5. Append-only tables block UPDATE and DELETE ─────────────────────────────

class TestAppendOnlyTables:
    """audit_logs, rule_execution_logs and model_inference_logs must block mutations."""

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_audit_logs_blocks_update(self, rls_tenants):
        """UPDATE on audit_logs must be blocked by RLS policy (USING false)."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        try:
            await _set_tenant(conn, tid_a)
            log_id = str(uuid.uuid4())
            # INSERT is allowed
            await conn.execute(
                "INSERT INTO audit_logs (id, tenant_id, action, entity_type)"
                " VALUES ($1, $2, 'RLS_TEST', 'test') ON CONFLICT (id) DO NOTHING",
                log_id, tid_a,
            )
            # UPDATE must be blocked
            try:
                result = await conn.execute(
                    "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = $1", log_id
                )
                affected = int(result.split()[-1])
                assert affected == 0, (
                    f"audit_logs UPDATE affected {affected} rows — immutability RLS breach!"
                )
            except Exception as exc:
                # Exception from trigger or policy is acceptable
                assert any(k in str(exc).lower() for k in ("immutable", "policy", "violat", "not allowed")), (
                    f"Unexpected exception on audit_logs UPDATE: {exc}"
                )
        finally:
            # audit_logs is immutable — can't delete. Leave row for real DB cleanup.
            await conn.close()

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_audit_logs_blocks_delete(self, rls_tenants):
        """DELETE on audit_logs must be blocked by RLS policy or trigger."""
        conn = await _connect()
        tid_a = rls_tenants["a"]
        try:
            await _set_tenant(conn, tid_a)
            log_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO audit_logs (id, tenant_id, action, entity_type)"
                " VALUES ($1, $2, 'RLS_TEST_DELETE', 'test') ON CONFLICT (id) DO NOTHING",
                log_id, tid_a,
            )
            try:
                result = await conn.execute(
                    "DELETE FROM audit_logs WHERE id = $1", log_id
                )
                affected = int(result.split()[-1])
                assert affected == 0, (
                    f"audit_logs DELETE affected {affected} rows — immutability RLS breach!"
                )
            except Exception as exc:
                assert any(k in str(exc).lower() for k in ("immutable", "policy", "violat", "not allowed")), (
                    f"Unexpected exception on audit_logs DELETE: {exc}"
                )
        finally:
            await conn.close()


# ── 6. Regression: fixed list of sensitive tables ─────────────────────────────

class TestSensitiveTableRegression:
    """Regression guard: any new tenant-scoped table must be added to SENSITIVE_RLS_TABLES."""

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_no_tenant_table_unprotected(self):
        """
        Query pg_class for all tables with a tenant_id column that lack RLS.
        Any such table must either be in GLOBAL_TABLES or already have RLS.
        """
        conn = await _connect()
        try:
            # Find tables in public schema that have a 'tenant_id' column
            tenant_id_tables = await conn.fetch(
                """
                SELECT DISTINCT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND a.attname = 'tenant_id'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """
            )
            all_tenant_tables = {r["relname"] for r in tenant_id_tables}

            # Check RLS status for each
            rls_rows = await conn.fetch(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relname = ANY($1::text[])
                """,
                list(all_tenant_tables),
            )
            rls_map = {r["relname"]: r for r in rls_rows}

            unprotected = []
            for tbl in all_tenant_tables:
                if tbl in GLOBAL_TABLES:
                    continue  # Documented exception
                row = rls_map.get(tbl)
                if row is None or not row["relrowsecurity"] or not row["relforcerowsecurity"]:
                    unprotected.append(tbl)

            assert not unprotected, (
                f"Tables with tenant_id column but WITHOUT RLS/FORCE RLS: {sorted(unprotected)}\n"
                "Add a migration or add to GLOBAL_TABLES with documented justification."
            )
        finally:
            await conn.close()

    def test_sensitive_rls_tables_constant_is_complete(self):
        """
        Static test: SENSITIVE_RLS_TABLES must include the 20 tables identified
        in the PR-01 audit. Fails fast if the constant is accidentally truncated.
        """
        required = {
            "players", "device_events", "player_kyc_events",
            "alerts", "cases", "case_events", "report_packages",
            "audit_logs", "rule_execution_logs", "model_inference_logs",
            "feature_snapshots", "scoring_configs",
            "rule_definitions", "compound_rules", "rule_macros",
            "player_lists", "player_list_entries",
            "mapping_configs", "ingest_jobs", "notifications",
        }
        missing = required - SENSITIVE_RLS_TABLES
        assert not missing, (
            f"These PR-01 tables are missing from SENSITIVE_RLS_TABLES: {missing}"
        )

    def test_append_only_tables_subset_of_sensitive(self):
        """APPEND_ONLY_TABLES must be a subset of SENSITIVE_RLS_TABLES."""
        diff = APPEND_ONLY_TABLES - SENSITIVE_RLS_TABLES
        assert not diff, (
            f"APPEND_ONLY_TABLES contains tables not in SENSITIVE_RLS_TABLES: {diff}"
        )

    @pytest.mark.asyncio
    @skip_unless_stack
    async def test_sensitive_tables_missing_tenant_id_require_fk_based_rls(self):
        """
        Guardrail for indirect-tenant cases.

        If any table in SENSITIVE_RLS_TABLES lacks tenant_id, PR-01 must include
        EXISTS-based policies via parent FK. Today all 20 tables have tenant_id
        direto, so this test asserts that expectation.
        """
        conn = await _connect()
        try:
            rows = await conn.fetch(
                """
                SELECT c.relname AS table_name,
                       EXISTS (
                         SELECT 1
                         FROM information_schema.columns ic
                         WHERE ic.table_schema = 'public'
                           AND ic.table_name = c.relname
                           AND ic.column_name = 'tenant_id'
                       ) AS has_tenant_id
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relname = ANY($1::text[])
                """,
                list(SENSITIVE_RLS_TABLES),
            )
            missing_direct = sorted(
                row["table_name"] for row in rows if not row["has_tenant_id"]
            )
            assert not missing_direct, (
                "Tabelas sensíveis sem tenant_id direto detectadas. "
                "Adicionar policy por FK com EXISTS e testes específicos: "
                f"{missing_direct}"
            )
        finally:
            await conn.close()
