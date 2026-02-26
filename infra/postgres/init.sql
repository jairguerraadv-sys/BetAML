-- Postgres init script
-- NOTE: Actual schema is managed by Alembic migrations.
-- This file only ensures required extensions are available.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
