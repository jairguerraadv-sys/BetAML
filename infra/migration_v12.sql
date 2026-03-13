-- migration_v12.sql — Alert label_note column
-- Adds analyst investigation note to alert labels (LGPD compliance + feedback loop)
-- Applied after migration_v11.sql

BEGIN;

-- Add label_note to alerts for analyst investigation notes
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS label_note TEXT;

COMMIT;
