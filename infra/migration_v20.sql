-- Migration v20: permitir status PARTIAL em ingest_jobs
--
-- A API usa status PARTIAL quando um lote possui registros aceitos e falhos.
-- Bases legadas podiam ter constraint sem PARTIAL, causando 409 em /ingest/connectors/*/parse.

ALTER TABLE ingest_jobs
    DROP CONSTRAINT IF EXISTS ingest_jobs_status_check;

ALTER TABLE ingest_jobs
    ADD CONSTRAINT ingest_jobs_status_check
    CHECK (status IN ('QUEUED', 'PROCESSING', 'DONE', 'FAILED', 'PARTIAL'));
