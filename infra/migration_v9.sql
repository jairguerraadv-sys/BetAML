-- ============================================================
-- BetAML — Migration v9
-- 1. Add notifications.reference_type / reference_id columns
-- 2. Add CHECK constraint for Player.status including ERASED
-- ============================================================

-- 1. Notifications reference columns (GAP-12)
--    These allow notifications to link to specific alerts or cases.
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS reference_type TEXT,
    ADD COLUMN IF NOT EXISTS reference_id   TEXT;

CREATE INDEX IF NOT EXISTS idx_notifications_reference
    ON notifications(reference_type, reference_id)
    WHERE reference_type IS NOT NULL;

-- 2. Player status — add ERASED to legitimate values (GAP-17)
--    Document the valid states and block arbitrary strings at DB level.
--    Existing constraint dropped first to avoid conflict if re-run.
ALTER TABLE players
    DROP CONSTRAINT IF EXISTS chk_player_status;

ALTER TABLE players
    ADD CONSTRAINT chk_player_status
    CHECK (status IN (
        'ACTIVE',
        'INACTIVE',
        'BLOCKED',
        'ERASED',
        'PENDING_REVIEW',
        'WATCHLIST'
    ));

-- 3. Index to fast-skip ERASED players in normal queries (GAP-17)
CREATE INDEX IF NOT EXISTS idx_players_status_not_erased
    ON players(tenant_id, status)
    WHERE status != 'ERASED';
