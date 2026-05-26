-- migration_v36: add integrity_hash to report_packages for chain-of-custody (COAF compliance)
BEGIN;

ALTER TABLE report_packages
    ADD COLUMN IF NOT EXISTS integrity_hash VARCHAR(64) NOT NULL DEFAULT '';

COMMIT;
