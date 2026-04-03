-- migration_v23.sql — Fase 3: UX, Governança, Observabilidade
--
-- Índices de suporte às novas queries:
--   1. Grafo de rede (GET /players/{id}/network) — device_hash, ip_hash, bank_account_hash
--   2. Data quality dashboard (GET /stats/data-quality) — feature_snapshots, ingest_errors
--   3. PLD KPI funnel (GET /stats/pld-kpis) — report_packages, alerts.label
--
-- NOTA: Removido CONCURRENTLY dos CREATE INDEX pois docker-entrypoint
-- executa cada .sql em transação implícita (--single-transaction).
-- Em produção, usar CONCURRENTLY manualmente fora de transação.
-- ============================================================

-- ── device_events ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_device_events_device_hash
    ON device_events (tenant_id, device_hash, player_id)
    WHERE device_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_device_events_ip_hash
    ON device_events (tenant_id, ip_hash, player_id)
    WHERE ip_hash IS NOT NULL;

-- ── financial_transactions ────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_financial_transactions_bank_account_hash
    ON financial_transactions (tenant_id, bank_account_hash, player_id)
    WHERE bank_account_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_financial_transactions_payment_instrument
    ON financial_transactions (tenant_id, payment_instrument, player_id)
    WHERE payment_instrument IS NOT NULL;

-- ── feature_snapshots ─────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_tenant_created
    ON feature_snapshots (tenant_id, created_at);

-- ── ingest_errors ─────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_ingest_errors_tenant_created
    ON ingest_errors (tenant_id, created_at);

-- ── report_packages ───────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_report_packages_tenant_status_created
    ON report_packages (tenant_id, status, created_at);

-- ── alerts — suporte precision label ─────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_alerts_tenant_label_created
    ON alerts (tenant_id, label, created_at)
    WHERE label IS NOT NULL;

-- ============================================================
-- Verificação (executar manualmente após aplicar):
--
--   SELECT schemaname, tablename, indexname, indexdef
--   FROM pg_indexes
--   WHERE indexname LIKE 'idx_%'
--   ORDER BY tablename, indexname;
-- ============================================================
