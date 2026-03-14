-- migration_v14.sql — Module 5: SLA dashboard + notification bell indexes
-- Run: psql $DATABASE_URL -f infra/migration_v14.sql
BEGIN;

-- Efficient SLA dashboard: active cases approaching/breaching deadline per tenant
CREATE INDEX IF NOT EXISTS idx_cases_sla_active
    ON cases (tenant_id, sla_due_at, status)
    WHERE status NOT IN ('CLOSED', 'REPORTED') AND sla_due_at IS NOT NULL;

-- Fast unread notification bell count per user
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id, is_read, created_at DESC)
    WHERE is_read = FALSE;

-- Fast comment/event retrieval per case ordered by time
CREATE INDEX IF NOT EXISTS idx_case_events_case_type
    ON case_events (case_id, event_type, created_at DESC);

COMMENT ON INDEX idx_cases_sla_active IS
    'Module 5 — SLA dashboard: active cases sorted by deadline';
COMMENT ON INDEX idx_notifications_user_unread IS
    'Module 5 — notification bell: unread count per user';
COMMENT ON INDEX idx_case_events_case_type IS
    'Module 5 — case timeline: events by case + type + time';

COMMIT;
