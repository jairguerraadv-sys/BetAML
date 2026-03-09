-- Migration v6: Align scoring_configs with ORM model
-- Adds: low_threshold, medium_threshold, high_threshold, critical_threshold, is_active, data_retention_days

ALTER TABLE scoring_configs
    ADD COLUMN IF NOT EXISTS low_threshold       NUMERIC(5,2) NOT NULL DEFAULT 30.0,
    ADD COLUMN IF NOT EXISTS medium_threshold    NUMERIC(5,2) NOT NULL DEFAULT 60.0,
    ADD COLUMN IF NOT EXISTS high_threshold      NUMERIC(5,2) NOT NULL DEFAULT 80.0,
    ADD COLUMN IF NOT EXISTS critical_threshold  NUMERIC(5,2) NOT NULL DEFAULT 95.0,
    ADD COLUMN IF NOT EXISTS is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS data_retention_days INTEGER      NOT NULL DEFAULT 1825;
