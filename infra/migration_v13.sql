-- BetAML Migration v13
-- GAP-9:  Add cnpj column to tenants (required for COAF RIF CnpjOuCpfComunicante)
-- GAP-10: Add pii_accessed column to audit_logs (LGPD Art. 37 — queryable PII trail)
-- Created: 2026-03-13

BEGIN;

-- GAP-9: Tenant CNPJ (14 digits, no punctuation)
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS cnpj VARCHAR(14);

COMMENT ON COLUMN tenants.cnpj IS
    'CNPJ do operador (14 dígitos sem pontuação) — obrigatório para geração de XML COAF (MIFD v3)';

-- GAP-10: Dedicated PII access column for LGPD compliance reports
ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS pii_accessed TEXT;

COMMENT ON COLUMN audit_logs.pii_accessed IS
    'Campo de PII acessado — cpf, cpf_masked, full_name, etc. (LGPD Art. 37 rastreabilidade)';

CREATE INDEX IF NOT EXISTS idx_audit_logs_pii_accessed
    ON audit_logs (tenant_id, pii_accessed)
    WHERE pii_accessed IS NOT NULL;

COMMIT;
