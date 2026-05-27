"""Complete RLS coverage for all tenant-scoped sensitive tables.

PR-01 — hardening/rls-complete-tables

Before this migration, 9 tables had RLS/FORCE RLS and 20 tenant-scoped
tables were unprotected. This migration enables RLS + FORCE RLS on all
remaining sensitive tables and creates per-operation policies using the
project's established current_tenant_id() function.

Tables added in this migration:
    PII CRÍTICA  : players
    PII ALTA     : device_events, player_kyc_events
    REGULATÓRIA  : alerts, cases, case_events, report_packages
    COMPLIANCE   : audit_logs  (SELECT+INSERT; UPDATE+DELETE blocked via policy;
                                immutability trigger already exists from
                                migration 20260519_000001 — PR-08 will add
                                a dedicated BEFORE DELETE/UPDATE trigger)
    AUDITORIA    : rule_execution_logs  (append-only: UPDATE+DELETE blocked)
    OPERACIONAL  : model_inference_logs (append-only: UPDATE+DELETE blocked)
                   feature_snapshots, scoring_configs
                   rule_definitions, compound_rules, rule_macros
                   player_lists, player_list_entries
                   mapping_configs, ingest_jobs, notifications

All tables have a direct tenant_id column confirmed in services/api/models.py.
No indirect FK-only cases required.

Policy naming convention (consistent with existing migrations):
    tenant_isolation_<table>_select
    tenant_isolation_<table>_insert
    tenant_isolation_<table>_update
    tenant_isolation_<table>_delete

Idempotency: all statements wrapped in DO $$ IF to_regclass() ... END $$
and preceded by DROP POLICY IF EXISTS.

Revision ID: 20260526_000002
Revises: 20260526_000001
Create Date: 2026-05-26 00:02:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260526_000002"
down_revision = "20260526_000001"
branch_labels = None
depends_on = None

# ── Helper: idempotent ENABLE + FORCE RLS + 4-operation policies ──────────────

_STANDARD_BLOCK = """\
DO $$
BEGIN
    IF to_regclass('public.{tbl}') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY';

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_select ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_select ON {tbl}
                FOR SELECT
                USING (tenant_id = current_tenant_id())
        $pol$;

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_insert ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_insert ON {tbl}
                FOR INSERT WITH CHECK (tenant_id = current_tenant_id())
        $pol$;

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_update ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_update ON {tbl}
                FOR UPDATE
                USING (tenant_id = current_tenant_id())
                WITH CHECK (tenant_id = current_tenant_id())
        $pol$;

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_delete ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_delete ON {tbl}
                FOR DELETE USING (tenant_id = current_tenant_id())
        $pol$;
    END IF;
END
$$;
"""

# Append-only tables: SELECT + INSERT allowed; UPDATE + DELETE blocked entirely.
# This is belt-and-suspenders atop any application-level restrictions.
_APPEND_ONLY_BLOCK = """\
DO $$
BEGIN
    IF to_regclass('public.{tbl}') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY';

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_select ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_select ON {tbl}
                FOR SELECT
                USING (tenant_id = current_tenant_id())
        $pol$;

        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_insert ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_insert ON {tbl}
                FOR INSERT WITH CHECK (tenant_id = current_tenant_id())
        $pol$;

        -- UPDATE blocked: no policy → FORCE RLS denies all updates.
        -- (Explicit USING (false) policy is clearer than silence.)
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_update ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_update ON {tbl}
                FOR UPDATE USING (false)
        $pol$;

        -- DELETE blocked: belt-and-suspenders alongside app-level restrictions.
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_{tbl}_delete ON {tbl}';
        EXECUTE $pol$
            CREATE POLICY tenant_isolation_{tbl}_delete ON {tbl}
                FOR DELETE USING (false)
        $pol$;
    END IF;
END
$$;
"""


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure current_tenant_id() exists (idempotent; originally created in
    # 20260526_000001 but we recreate for resilience in replay scenarios).
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
            SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
        $$ LANGUAGE SQL STABLE;
    """))

    # ── 1. PII CRÍTICA ─────────────────────────────────────────────────────────
    # players: CPF+name encrypted, cpf_hmac, pep_flag, income — highest risk.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="players")))

    # ── 2. PII ALTA ────────────────────────────────────────────────────────────
    # device_events: fingerprint, IP hash, geolocation, session.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="device_events")))

    # player_kyc_events: KYC lifecycle, document types, pep_flag, income.
    # Note: player_id is TEXT (not UUID FK) in this table per migration_v27;
    # tenant_id is still a direct UUID FK — isolation is correct.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="player_kyc_events")))

    # ── 3. REGULATÓRIA ─────────────────────────────────────────────────────────
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="alerts")))
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="cases")))

    # case_events has both tenant_id direct and case_id FK; direct column used.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="case_events")))

    # report_packages: COAF packages, cadeia de custódia, protocol numbers.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="report_packages")))

    # ── 4. COMPLIANCE — audit_logs ─────────────────────────────────────────────
    # audit_logs is append-only for compliance.
    # An immutability trigger (prevent_audit_log_mutation) already exists from
    # migration 20260519_000001. The USING(false) policies below add a second
    # defence layer. Full audit-immutability hardening belongs to PR-08.
    conn.execute(sa.text(_APPEND_ONLY_BLOCK.format(tbl="audit_logs")))

    # ── 5. AUDITORIA — append-only logs ────────────────────────────────────────
    conn.execute(sa.text(_APPEND_ONLY_BLOCK.format(tbl="rule_execution_logs")))

    # ── 6. OPERACIONAL / ML ────────────────────────────────────────────────────
    # model_inference_logs: inference scores, anomaly flags — append-only.
    conn.execute(sa.text(_APPEND_ONLY_BLOCK.format(tbl="model_inference_logs")))

    # feature_snapshots: ML features per player — mutable (drift recalculation).
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="feature_snapshots")))

    # scoring_configs: has unique(tenant_id) — one row per tenant.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="scoring_configs")))

    # ── 7. OPERACIONAL — rules ─────────────────────────────────────────────────
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="rule_definitions")))
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="compound_rules")))
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="rule_macros")))

    # ── 8. OPERACIONAL — player lists ──────────────────────────────────────────
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="player_lists")))
    # player_list_entries has both list_id FK and direct tenant_id; direct used.
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="player_list_entries")))

    # ── 9. OPERACIONAL — ingest / config / notifications ───────────────────────
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="mapping_configs")))
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="ingest_jobs")))
    conn.execute(sa.text(_STANDARD_BLOCK.format(tbl="notifications")))


# ── Downgrade ─────────────────────────────────────────────────────────────────

_STANDARD_TABLES = [
    "players",
    "device_events",
    "player_kyc_events",
    "alerts",
    "cases",
    "case_events",
    "report_packages",
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
]

_APPEND_ONLY_TABLES = [
    "audit_logs",
    "rule_execution_logs",
    "model_inference_logs",
]


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in _STANDARD_TABLES + _APPEND_ONLY_TABLES:
        for op_name in ("select", "insert", "update", "delete"):
            conn.execute(sa.text(
                f"DROP POLICY IF EXISTS tenant_isolation_{tbl}_{op_name} ON {tbl}"
            ))
        conn.execute(sa.text(
            f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY"
        ))
        conn.execute(sa.text(
            f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY"
        ))
