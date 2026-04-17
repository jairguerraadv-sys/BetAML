-- BetAML migration v32
-- Completa o bootstrap legado do compose com external_validation_requests,
-- evitando create_all no startup da API com a role restrita da aplicacao.

CREATE TABLE IF NOT EXISTS external_validation_requests (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    player_id UUID NOT NULL,
    provider VARCHAR(40) NOT NULL DEFAULT 'mock_identity',
    validation_type VARCHAR(40) NOT NULL DEFAULT 'CPF_IDENTITY',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB DEFAULT '{}'::jsonb,
    external_request_id TEXT,
    error_message TEXT,
    requested_by UUID,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_external_validation_requests_player_id
    ON external_validation_requests(player_id);

CREATE INDEX IF NOT EXISTS ix_external_validation_requests_tenant_id
    ON external_validation_requests(tenant_id);

CREATE INDEX IF NOT EXISTS idx_ext_validation_idempotency_lookup
    ON external_validation_requests(tenant_id, player_id, provider, validation_type, requested_at);

CREATE INDEX IF NOT EXISTS idx_ext_validation_status_requested_at
    ON external_validation_requests(status, requested_at);

ALTER TABLE external_validation_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE external_validation_requests FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_external_validation_requests ON external_validation_requests;
CREATE POLICY tenant_isolation_external_validation_requests
    ON external_validation_requests
    USING (tenant_id = current_tenant_id());

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'betaml_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE external_validation_requests TO betaml_app;
    END IF;
END
$$;