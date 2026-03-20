-- Migration v18: Hardening de isolamento multi-tenant e trilha de auditoria imutavel
--
-- Objetivos:
-- 1) Garantir RLS + FORCE RLS em tabelas tenant-scoped que ainda podiam ficar sem enforcement.
-- 2) Bloquear UPDATE/DELETE em audit_logs para preservar evidencias regulatorias.

-- Reaproveita helper se necessario
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
    SELECT NULLIF(current_setting('app.current_tenant', TRUE), '')::UUID;
$$ LANGUAGE SQL STABLE;

DO $$
BEGIN
    IF to_regclass('public.api_keys') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE api_keys FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_api_keys ON api_keys';
        EXECUTE 'CREATE POLICY tenant_isolation_api_keys ON api_keys USING (tenant_id = current_tenant_id())';
    END IF;

    IF to_regclass('public.ingest_errors') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE ingest_errors ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE ingest_errors FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_ingest_errors ON ingest_errors';
        EXECUTE 'CREATE POLICY tenant_isolation_ingest_errors ON ingest_errors USING (tenant_id = current_tenant_id())';
    END IF;

    IF to_regclass('public.external_validation_requests') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE external_validation_requests ENABLE ROW LEVEL SECURITY';
        EXECUTE 'ALTER TABLE external_validation_requests FORCE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS tenant_isolation_external_validation_requests ON external_validation_requests';
        EXECUTE 'CREATE POLICY tenant_isolation_external_validation_requests ON external_validation_requests USING (tenant_id = current_tenant_id())';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is immutable: operation % is not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_prevent_audit_log_update_delete ON audit_logs;
CREATE TRIGGER trg_prevent_audit_log_update_delete
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW
EXECUTE FUNCTION prevent_audit_log_mutation();
