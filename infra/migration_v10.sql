-- ============================================================
-- BetAML — Migration v10 — feature_version column on feature_snapshots
-- Adds the feature_version column required by FeatureSnapshotOut schema
-- and the FeatureSnapshot ORM model (default = 2 for V2 features).
-- ============================================================

ALTER TABLE feature_snapshots
    ADD COLUMN IF NOT EXISTS feature_version INTEGER NOT NULL DEFAULT 2;

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_version
    ON feature_snapshots(tenant_id, player_id, feature_version);
