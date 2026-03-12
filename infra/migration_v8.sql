-- ============================================================
-- BetAML — Migration v8 — Notifications read flag alignment
-- Align legacy notifications.read with ORM notifications.is_read
-- ============================================================

ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS is_read BOOLEAN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'notifications'
          AND column_name = 'read'
    ) THEN
        EXECUTE '
            UPDATE notifications
            SET is_read = COALESCE(is_read, read, FALSE)
            WHERE is_read IS NULL OR is_read IS DISTINCT FROM read
        ';
    ELSE
        UPDATE notifications
        SET is_read = FALSE
        WHERE is_read IS NULL;
    END IF;
END $$;

ALTER TABLE notifications
    ALTER COLUMN is_read SET DEFAULT FALSE;

UPDATE notifications
SET is_read = FALSE
WHERE is_read IS NULL;

ALTER TABLE notifications
    ALTER COLUMN is_read SET NOT NULL;

DROP INDEX IF EXISTS idx_notifications_user;
CREATE INDEX IF NOT EXISTS idx_notifications_user
    ON notifications(user_id, is_read, created_at DESC);