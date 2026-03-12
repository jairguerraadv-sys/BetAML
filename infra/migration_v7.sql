ALTER TABLE feature_snapshots
    ADD COLUMN IF NOT EXISTS snapshot_date DATE;

UPDATE feature_snapshots
SET snapshot_date = feature_date
WHERE snapshot_date IS NULL;

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_tenant_player_snapshot_date
    ON feature_snapshots (tenant_id, player_id, snapshot_date DESC);