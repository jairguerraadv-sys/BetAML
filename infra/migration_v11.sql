-- Migration v11: Performance indexes for high-volume queries
-- Run with: psql -U betaml -d betaml_dev -f migration_v11.sql

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════════
-- Alerts: principais queries de listagem e filtragem
-- ═══════════════════════════════════════════════════════════════════════════

-- Listagem de alerts por tenant + status + ordenação por data
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_status_created
ON alerts(tenant_id, status, created_at DESC);

-- Filtro por severidade (dashboard, relatórios)
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_severity
ON alerts(tenant_id, severity);

-- Lookup de alerts por player_id (player profile)
CREATE INDEX IF NOT EXISTS idx_alerts_tenant_player
ON alerts(tenant_id, player_id);

-- Alerts vinculados a cases
CREATE INDEX IF NOT EXISTS idx_alerts_case_id
ON alerts(case_id) WHERE case_id IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════════
-- Cases: listagem com SLA, status, assigned_to
-- ═══════════════════════════════════════════════════════════════════════════

-- Listagem de cases por tenant + status + SLA vencido
CREATE INDEX IF NOT EXISTS idx_cases_tenant_status_sla
ON cases(tenant_id, status, sla_due_at DESC NULLS LAST);

-- Cases atribuídos a um analista (My Cases)
CREATE INDEX IF NOT EXISTS idx_cases_tenant_assigned
ON cases(tenant_id, assigned_to) WHERE assigned_to IS NOT NULL;

-- Cases por prioridade (triage workflow)
CREATE INDEX IF NOT EXISTS idx_cases_tenant_priority
ON cases(tenant_id, priority);

-- ═══════════════════════════════════════════════════════════════════════════
-- Players: risk score, customer ID, status
-- ═══════════════════════════════════════════════════════════════════════════

-- Ordenação por risk_score (top riskiest players)
CREATE INDEX IF NOT EXISTS idx_players_tenant_risk_score
ON players(tenant_id, risk_score DESC NULLS LAST);

-- Lookup por customer_id (ID do backoffice)
CREATE INDEX IF NOT EXISTS idx_players_tenant_customer_id
ON players(tenant_id, customer_id);

-- Players com status específico (ACTIVE, SUSPENDED, ERASED)
CREATE INDEX IF NOT EXISTS idx_players_tenant_status
ON players(tenant_id, status);

-- Search por CPF encrypted (exact match, não usado para LIKE)
CREATE INDEX IF NOT EXISTS idx_players_tenant_cpf
ON players(tenant_id, cpf_encrypted);

-- ═══════════════════════════════════════════════════════════════════════════
-- FinancialTransactions: queries em player timeline
-- ═══════════════════════════════════════════════════════════════════════════

-- Timeline de transações por player (DESC ordem cronológica)
CREATE INDEX IF NOT EXISTS idx_financial_transactions_tenant_player_ts
ON financial_transactions(tenant_id, player_id, transaction_timestamp DESC);

-- Filtro por tipo de transação (DEPOSIT, WITHDRAWAL, etc.)
CREATE INDEX IF NOT EXISTS idx_financial_transactions_tenant_type
ON financial_transactions(tenant_id, transaction_type);

-- Transações processadas (status COMPLETED vs PENDING)
CREATE INDEX IF NOT EXISTS idx_financial_transactions_tenant_status
ON financial_transactions(tenant_id, status);

-- ═══════════════════════════════════════════════════════════════════════════
-- Bets: apostas por player
-- ═══════════════════════════════════════════════════════════════════════════

-- Timeline de bets por player
CREATE INDEX IF NOT EXISTS idx_bets_tenant_player_ts
ON bets(tenant_id, player_id, placed_at DESC);

-- Bets settled (payout calculado)
CREATE INDEX IF NOT EXISTS idx_bets_tenant_settled
ON bets(tenant_id, settled) WHERE settled = TRUE;

-- ═══════════════════════════════════════════════════════════════════════════
-- AuditLog: compliance queries (filtro por entity, user, ação)
-- ═══════════════════════════════════════════════════════════════════════════

-- Audit logs por tenant + timestamp (relatórios mensais)
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_created
ON audit_logs(tenant_id, created_at DESC);

-- Audit por entity_type (ex: todas as ações em "Player")
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_entity
ON audit_logs(tenant_id, entity_type);

-- Audit por user_id (quem fez o quê)
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_user
ON audit_logs(tenant_id, user_id);

-- Audit de acesso PII (LGPD Art. 37)
CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_pii
ON audit_logs(tenant_id, pii_accessed) WHERE pii_accessed = TRUE;

-- ═══════════════════════════════════════════════════════════════════════════
-- IngestJob: tracking de jobs de ingestão
-- ═══════════════════════════════════════════════════════════════════════════

-- Jobs por tenant + status + data
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_tenant_status_created
ON ingest_jobs(tenant_id, status, created_at DESC);

-- Jobs por source system (filtro no dashboard)
CREATE INDEX IF NOT EXISTS idx_ingest_jobs_tenant_source
ON ingest_jobs(tenant_id, source_system);

-- ═══════════════════════════════════════════════════════════════════════════
-- IngestError: DLQ errors
-- ═══════════════════════════════════════════════════════════════════════════

-- Errors por job_id
CREATE INDEX IF NOT EXISTS idx_ingest_errors_tenant_job
ON ingest_errors(tenant_id, ingest_job_id);

-- Errors não resolvidos (resolved = FALSE)
CREATE INDEX IF NOT EXISTS idx_ingest_errors_tenant_resolved
ON ingest_errors(tenant_id, resolved) WHERE resolved = FALSE;

-- ═══════════════════════════════════════════════════════════════════════════
-- FeatureSnapshot: histórico de features por player
-- ═══════════════════════════════════════════════════════════════════════════

-- Snapshots por player + data (feature store history endpoint)
CREATE INDEX IF NOT EXISTS idx_feature_snapshots_tenant_player_date
ON feature_snapshots(tenant_id, player_id, snapshot_date DESC NULLS LAST);

-- ═══════════════════════════════════════════════════════════════════════════
-- Notifications: notificações não lidas
-- ═══════════════════════════════════════════════════════════════════════════

-- Notifications por tenant + is_read (fetchNotifications com filtro)
CREATE INDEX IF NOT EXISTS idx_notifications_tenant_read_created
ON notifications(tenant_id, is_read, created_at DESC);

-- ═══════════════════════════════════════════════════════════════════════════
-- RuleDefinitions: regras ativas
-- ═══════════════════════════════════════════════════════════════════════════

-- Regras ativas por tenant (rules engine fetch)
CREATE INDEX IF NOT EXISTS idx_rule_definitions_tenant_active
ON rule_definitions(tenant_id, is_active) WHERE is_active = TRUE;

-- ═══════════════════════════════════════════════════════════════════════════
-- Vacuum e Analyze (otimização de statistics para query planner)
-- ═══════════════════════════════════════════════════════════════════════════

VACUUM ANALYZE alerts;
VACUUM ANALYZE cases;
VACUUM ANALYZE players;
VACUUM ANALYZE financial_transactions;
VACUUM ANALYZE bets;
VACUUM ANALYZE audit_logs;
VACUUM ANALYZE ingest_jobs;
VACUUM ANALYZE feature_snapshots;

COMMIT;

-- ═══════════════════════════════════════════════════════════════════════════
-- Verificação de índices criados
-- ═══════════════════════════════════════════════════════════════════════════
-- SELECT tablename, indexname, indexdef
-- FROM pg_indexes
-- WHERE schemaname = 'public'
-- ORDER BY tablename, indexname;
