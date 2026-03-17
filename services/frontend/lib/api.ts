import axios from 'axios';

// Sempre usa o proxy local — o servidor Next.js encaminha para a API.
// Isso garante que localhost:8000 nunca é chamado direto do browser,
// resolvendo ERR_CONNECTION_REFUSED em Codespaces/devcontainer.
const BASE = '/api-proxy';

export const api = axios.create({ baseURL: BASE });

// O JWT é transportado como cookie httpOnly (setado via /api/auth/login).
// O middleware Next.js (middleware.ts) injeta automaticamente o header
// Authorization: Bearer <token> nas chamadas /api-proxy/*.
// NENHUM código JS no browser tem acesso direto ao token — imune a XSS.

// Redireciona para login em 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && typeof window !== 'undefined') {
      window.location.href = '/login';
    }
    return Promise.reject(err);
  },
);

// ── Auth ──────────────────────────────────────────────────────────────────────

/** Resposta da API route Next.js /api/auth/login (sem o token — fica no cookie). */
export interface LoginResponse {
  role: string;
  tenant_id: string;
}

/**
 * Login via Next.js API route que seta cookie httpOnly.
 * NÃO chama o backend diretamente — usa /api/auth/login como proxy server-side.
 */
export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? 'Falha no login');
  }
  return res.json() as Promise<LoginResponse>;
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' });
}

export async function refreshToken() {
  const { data } = await api.post('/auth/refresh');
  return data as { access_token: string; token_type: string };
}

export interface AuditLog {
  id: string;
  user_id?: string | null;
  actor_id?: string | null;
  action: string;
  entity_type: string;
  entity_id?: string | null;
  ip_address?: string | null;
  pii_accessed?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  created_at: string;
}

export const fetchAuditLogs = (params?: Record<string, string>) =>
  api.get<AuditLog[]>('/audit-logs', { params }).then((r) => r.data);

// ── Types ─────────────────────────────────────────────────────────────────────

export interface User {
  id: string; email: string; username: string; role: string; tenant_id: string;
}

export interface Alert {
  id: string; title: string; severity: string; status: string;
  player_id: string; alert_type: string; created_at: string; rule_id?: string;
  anomaly_score?: number; case_id?: string;
}

export interface AlertDetail extends Alert {
  description?: string;
  evidence: Record<string, unknown>;
  source_event_id?: string;
  composite_score?: number;
  score_breakdown?: Record<string, unknown>;
  triaged_by?: string;
  triaged_at?: string;
  label?: string;
  labeled_at?: string;
}

export interface Case {
  id: string; reference_number: string; title: string; status: string;
  assigned_to?: string; created_at: string; priority: string;
  severity?: string; player_id?: string; auto_created?: boolean;
  sla_due_at?: string;
}

export interface CaseDetail extends Case {
  description?: string;
  alerts: Array<{ id: string; severity: string; title: string }>;
  timeline: Array<{ id: string; event_type: string; content: Record<string, unknown>; created_at: string }>;
}

export interface Player {
  id: string;
  external_player_id: string;
  cpf_masked: string;
  pep_flag: boolean;
  risk_score: number;
  risk_band: 'LOW' | 'MEDIUM' | 'HIGH';
  created_at: string;
}

export interface PlayerDetail {
  id: string; external_player_id: string; cpf: string; pep_flag: boolean;
  risk_score: number; risk_band: 'LOW' | 'MEDIUM' | 'HIGH';
  declared_income_monthly: number | null; last_scored_at: string | null;
}

export interface FeatureStoreCurrent {
  player_id: string;
  source: string;
  feature_version: number;
  computed_at?: string;
  features: Record<string, unknown>;
}

export interface FeatureStoreHistoryItem {
  id: string;
  snapshot_date: string | null;
  created_at: string;
  features: Record<string, unknown>;
  drift_score?: number | null;
  feature_version: number;
}

export interface FeatureStoreHistory {
  player_id: string;
  from?: string | null;
  to?: string | null;
  count: number;
  items: FeatureStoreHistoryItem[];
}

export interface FeatureStat {
  mean: number;
  std: number;
  p10: number;
  p25: number;
  p50: number;
  p75: number;
  p90: number;
  count: number;
}

export interface FeaturePopulationStats {
  computed_at: string | null;
  features: Record<string, FeatureStat>;
}

export interface EconCompat {
  player_id: string;
  declared_income_monthly: number | null;
  deposit_sum_30d: number;
  income_ratio_30d: number | null;
  ratio_threshold: number;
  tier: 'GREEN' | 'YELLOW' | 'RED' | 'UNKNOWN';
  interpretation: string;
}

export interface ReportPackageResult {
  report_package_id: string;
  status: string;
  decision: string;
  pdf_path: string | null;
  payload: Record<string, unknown>;
}

export interface RelatedTransactions {
  alert_id: string;
  player_id: string;
  window_hours: number;
  transactions: Array<{
    id: string; type: string; amount: number; currency: string;
    status: string; payment_method: string | null; occurred_at: string;
  }>;
  bets: Array<{
    id: string; bet_type: string; stake_amount: number;
    actual_payout: number | null; status: string;
    event_name: string | null; occurred_at: string;
  }>;
}

export interface Rule {
  id: string; name: string; description?: string; scope: string;
  condition_dsl: string; severity: string; status: string; version: number;
}

// ── Resources ─────────────────────────────────────────────────────────────────

export const fetchAlerts = (params?: Record<string, string>) =>
  api.get<{ total: number; items: Alert[] }>('/alerts', { params }).then((r) => r.data);

export const fetchAlert = (id: string) =>
  api.get<AlertDetail>(`/alerts/${id}`).then((r) => r.data);

export const fetchCases = (params?: Record<string, string | number>) =>
  api.get<Case[]>('/cases', { params }).then((r) => r.data);

export const fetchCase = (id: string) =>
  api.get<CaseDetail>(`/cases/${id}`).then((r) => r.data);

export const createCase = (body: {
  title: string;
  description?: string;
  player_id?: string;
  severity?: string;
}) => api.post<{ id: string; title: string; status: string }>('/cases', body).then((r) => r.data);

export const fetchPlayers = (params?: Record<string, string>) =>
  api.get<Player[]>('/players', { params }).then((r) => r.data);

export const fetchPlayer = (id: string) =>
  api.get<PlayerDetail>(`/players/${id}`).then((r) => r.data);

export const fetchFeatureStoreCurrent = (playerId: string) =>
  api.get<FeatureStoreCurrent>(`/feature-store/players/${playerId}/current`).then((r) => r.data);

export const fetchFeatureStoreHistory = (
  playerId: string,
  params?: { from?: string; to?: string },
) =>
  api.get<FeatureStoreHistory>(`/feature-store/players/${playerId}/history`, { params }).then((r) => r.data);

export const fetchFeaturePopulationStats = () =>
  api.get<FeaturePopulationStats>('/feature-store/population-stats').then((r) => r.data);

export const fetchPlayerEconCompat = (id: string) =>
  api.get<EconCompat>(`/players/${id}/econ-compat`).then((r) => r.data);

export const fetchAlertRelatedTransactions = (alertId: string) =>
  api.get<RelatedTransactions>(`/alerts/${alertId}/related-transactions`).then((r) => r.data);

export const generateReportPackage = (
  caseId: string,
  body: { analyst_narrative?: string; decision?: string },
) => api.post<ReportPackageResult>(`/cases/${caseId}/report-package`, body).then((r) => r.data);

export interface ScoringConfig {
  id: string;  // UUID
  rule_weight: number;
  ml_weight: number;
  network_weight: number;
  low_threshold: number;
  medium_threshold: number;
  high_threshold: number;
  critical_threshold: number;
  sla_low_hours: number;
  sla_medium_hours: number;
  sla_high_hours: number;
  sla_critical_hours: number;
  data_retention_days: number;
  data_retention_raw_years: number;
  data_retention_silver_years: number;
  data_retention_gold_years: number;
  updated_at: string | null;
}

export interface SensitivityPreview {
  current: { low: number; medium: number; high: number; critical: number };
  proposed: { low: number; medium: number; high: number; critical: number };
  total_alerts_30d: number;
}

export const fetchScoringConfig = () =>
  api.get<ScoringConfig>('/scoring-config').then((r) => r.data);

export const updateScoringConfig = (body: Partial<ScoringConfig>) =>
  api.put<ScoringConfig>('/scoring-config', body).then((r) => r.data);

export const fetchSensitivityPreview = (body: Partial<ScoringConfig>) =>
  api.post<SensitivityPreview>('/scoring-config/preview', body).then((r) => r.data);

export interface RuleCreatePayload {
  name: string;
  condition_dsl: string;
  severity: string;
  description?: string;
  scope?: string;
}

export const fetchRules = () => api.get<Rule[]>('/rules').then((r) => r.data);

export const createRule = (body: RuleCreatePayload) =>
  api.post<Rule>('/rules', body).then((r) => r.data);

export const validateDsl = (condition_dsl: string) =>
  api.post<{ valid: boolean; error?: string }>('/rules/validate', { condition_dsl }).then((r) => r.data);

export interface SimulateRuleResult {
  rule_id: string;
  results: Array<{ matched: boolean; event: Record<string, unknown>; error?: string }>;
  matches: number;
}

export const simulateRule = (id: string, payload: object) =>
  api.post<SimulateRuleResult>(`/rules/${id}/simulate`, payload).then((r) => r.data);

export const ingestFile = (formData: FormData) =>
  api
    .post<{ status: string; rows_processed?: number }>('/ingest/file', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);

export const triageAlert = (alertId: string, disposition: string, note: string) =>
  api.post(`/alerts/${alertId}/triage`, { disposition, note }).then((r) => r.data);

// ── Mappings (Módulo 1) ─────────────────────────────────────────────────────

export interface MappingTemplate {
  connector_name: string;
  source_system: string;
  format: 'json' | 'yaml';
  payload_format: string;
  content_type: string;
  auth_mode: string;
  signature_header?: string;
  timestamp_header?: string;
  template: string;
  sample_payload: string;
  input_schema: Array<{
    name: string;
    type: string;
    required: boolean;
    description: string;
  }>;
}

export interface MappingListItem {
  id: string;
  name: string;
  source_system: string;
  entity_type: string;
  version: string;
  version_number: number;
  is_current: boolean;
  active: boolean;
  change_notes?: string | null;
  updated_at?: string;
}

export interface MappingDetail extends MappingListItem {
  config_json: Record<string, unknown>;
}

export interface MappingVersion {
  id: string;
  name: string;
  version_number: number;
  is_current: boolean;
  change_notes?: string | null;
  created_at: string;
}

export interface MappingValidateResponse {
  valid: boolean;
  error?: string;
  normalized_config?: Record<string, unknown>;
}

export interface MappingPreviewResponse extends MappingValidateResponse {
  preview?: Record<string, unknown>;
}

export interface MappingCreatePayload {
  name: string;
  source_system: string;
  entity_type: string;
  config_text?: string;
  config_json?: Record<string, unknown>;
  format: 'json' | 'yaml';
  change_notes?: string;
}

export const fetchMappingTemplates = () =>
  api.get<MappingTemplate[]>('/mappings/templates').then((r) => r.data);

export const fetchMappings = () =>
  api.get<MappingListItem[]>('/mappings').then((r) => r.data);

export const fetchMapping = (id: string) =>
  api.get<MappingDetail>(`/mappings/${id}`).then((r) => r.data);

export const fetchMappingVersions = (id: string) =>
  api.get<MappingVersion[]>(`/mappings/${id}/versions`).then((r) => r.data);

export const validateMappingConfig = (body: {
  config_text?: string;
  config_json?: Record<string, unknown>;
  format: 'json' | 'yaml';
}) => api.post<MappingValidateResponse>('/mappings/validate', body).then((r) => r.data);

export const previewMappingConfig = (body: {
  config_text?: string;
  config_json?: Record<string, unknown>;
  sample: Record<string, unknown>;
  format: 'json' | 'yaml';
}) => api.post<MappingPreviewResponse>('/mappings/preview', body).then((r) => r.data);

export const createMapping = (body: MappingCreatePayload) =>
  api.post('/mappings', body).then((r) => r.data);

export const updateMappingAsNewVersion = (id: string, body: {
  name?: string;
  config_text?: string;
  config_json?: Record<string, unknown>;
  format: 'json' | 'yaml';
  change_notes?: string;
}) => api.put(`/mappings/${id}`, body).then((r) => r.data);

export const rollbackMappingVersion = (id: string, versionNumber: number) =>
  api.post(`/mappings/${id}/rollback`, null, { params: { version_number: versionNumber } }).then((r) => r.data);

// ── Notifications ───────────────────────────────────────────────────────────

export interface Notification {
  id: string;
  tenant_id: string;
  user_id: string | null;
  type: string;
  title: string;
  body: string | null;
  reference_type: string | null;
  reference_id: string | null;
  is_read: boolean;
  created_at: string;
  read_at: string | null;
}

export const fetchNotifications = (unreadOnly?: boolean) =>
  api.get<Notification[]>('/notifications', { params: unreadOnly ? { unread_only: true } : {} })
    .then((r) => r.data);

export const markNotificationRead = (id: string) =>
  api.post<{ status: string }>(`/notifications/${id}/read`).then((r) => r.data);

export const markAllNotificationsRead = () =>
  api.post<{ status: string }>('/notifications/read-all').then((r) => r.data);

// ── Model Registry ──────────────────────────────────────────────────────────

export interface ModelRegistry {
  id: string;
  tenant_id: string;
  model_name: string;
  model_type: string;
  version: string;
  training_rows: number | null;
  feature_columns: string[];
  metrics: Record<string, unknown>;
  status: string;
  is_challenger: boolean;
  promoted_by: string | null;
  promoted_at: string | null;
  trained_by: string | null;
  trained_at: string | null;
  created_at: string;
}

export const fetchModelRegistry = (modelType?: string) =>
  api.get<ModelRegistry[]>('/model-registry', { params: modelType ? { model_type: modelType } : {} })
    .then((r) => r.data);

export const promoteModel = (modelId: string) =>
  api.post<{ status: string; model_id: string }>(`/model-registry/${modelId}/promote`)
    .then((r) => r.data);

// ── Admin — Tenant Onboarding ───────────────────────────────────────────────

export interface TenantCreatePayload {
  name: string;
  slug: string;
  admin_username: string;
  admin_email: string;
  admin_password: string;
  risk_score_threshold?: number;
  cnpj?: string;
}

export interface TenantCreateResult {
  tenant_id: string;
  slug: string;
  admin_user_id: string;
  admin_username: string;
  message: string;
}

export const createTenant = (body: TenantCreatePayload) =>
  api.post<TenantCreateResult>('/admin/tenants', body).then((r) => r.data);

export interface TenantOut {
  id: string;
  name: string;
  slug: string;
  active: boolean;
  created_at: string;
  user_count?: number;
}

export const fetchTenants = () =>
  api.get<TenantOut[]>('/admin/tenants').then((r) => r.data);

export const updateTenant = (id: string, body: { name?: string; active?: boolean }) =>
  api.patch<TenantOut>(`/admin/tenants/${id}`, body).then((r) => r.data);

// ── Admin: User Management ────────────────────────────────────────────────────

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  role: string;
  active: boolean;
  created_at: string;
}

export interface AdminUserCreateIn {
  username: string;
  email: string;
  password: string;
  role: string;
}

export const fetchAdminUsers = () =>
  api.get<AdminUser[]>('/admin/users').then((r) => r.data);

export const createAdminUser = (body: AdminUserCreateIn) =>
  api.post<AdminUser>('/admin/users', body).then((r) => r.data);

export const updateAdminUser = (id: string, body: { role?: string; active?: boolean }) =>
  api.patch<AdminUser>(`/admin/users/${id}`, body).then((r) => r.data);

export const deleteAdminUser = (id: string) =>
  api.delete(`/admin/users/${id}`);

export const resetUserPassword = (id: string, new_password: string) =>
  api.post(`/admin/users/${id}/reset-password`, { new_password });

// ── Stats ─────────────────────────────────────────────────────────────────────

export interface DashboardStats {
  alerts_today:  number;
  critical_open: number;
  cases_open:    number;
  sla_expired:   number;
  auto_detected: number;
  by_severity:   Record<string, number>;
}

export const fetchDashboardStats = () =>
  api.get<DashboardStats>('/stats/dashboard').then((r) => r.data);

// ── Ingest Jobs & Errors (Módulo 1) ──────────────────────────────────────────

export type IngestJobStatus = 'QUEUED' | 'PROCESSING' | 'DONE' | 'PARTIAL' | 'FAILED';

export interface IngestJob {
  id: string;
  source_system: string;
  file_name: string | null;
  status: IngestJobStatus;
  total_records: number | null;
  processed_records: number | null;
  failed_records: number | null;
  bytes_processed: number;
  duration_ms: number | null;
  created_at: string;
  updated_at: string;
}

export interface IngestJobErrorSample {
  id: string;
  line_number: number | null;
  error_reason: string;
  raw_payload: string;
  created_at: string;
}

export interface IngestJobDetail extends IngestJob {
  error_count: number;
  error_sample: IngestJobErrorSample[];
  file_size_bytes: number | null;
  reprocessed_from: string | null;
  mapping_version_id: string | null;
  file_path: string | null;
}

export interface IngestError {
  id: string;
  ingest_job_id: string | null;
  source_system: string;
  entity_type: string | null;
  line_number: number | null;
  error_reason: string;
  error_detail: Record<string, unknown>;
  raw_payload: string;
  resolved: boolean;
  resolved_by: string | null;
  resolved_at: string | null;
  created_at: string;
}

export const fetchIngestJobs = (params?: {
  status?: string;
  source_system?: string;
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}) => api.get<IngestJob[]>('/ingest/jobs', { params }).then((r) => r.data);

export const fetchIngestJob = (jobId: string) =>
  api.get<IngestJobDetail>(`/ingest/jobs/${jobId}`).then((r) => r.data);

export const reprocessIngestJob = (
  jobId: string,
  body: { reason: string; mapping_version_id?: string },
) =>
  api.post<{ job_id: string; status: string }>(
    `/ingest/jobs/${jobId}/reprocess`,
    body,
  ).then((r) => r.data);

export const fetchIngestErrors = (params?: {
  source_system?: string;
  job_id?: string;
  resolved?: boolean;
  limit?: number;
  offset?: number;
}) => api.get<IngestError[]>('/ingest/errors', { params }).then((r) => r.data);

export const resolveIngestError = (errorId: string, body: { note?: string }) =>
  api.post<{ status: string; id: string }>(
    `/ingest/errors/${errorId}/resolve`,
    body,
  ).then((r) => r.data);

export const replayIngestError = (
  errorId: string,
  body: {
    corrected_payload: Record<string, unknown>;
    entity_type?: string;
    mapping_config_id?: string;
    resolve_original?: boolean;
    note?: string;
  },
) =>
  api.post<{ status: string; event_id: string; ingest_error_id: string; resolved: boolean }>(
    `/ingest/errors/${errorId}/replay`,
    body,
  ).then((r) => r.data);

// ── Module 5 — Case Workflow + Player Investigation ────────────────────────────

export interface TransactionChartItem {
  day: string;
  deposit_sum: number;
  withdrawal_sum: number;
}

export interface BetChartItem {
  day: string;
  stake_sum: number;
}

export interface PaymentInstrumentSummary {
  payment_instrument: string | null;
  payment_method: string | null;
  first_seen: string | null;
  last_seen: string | null;
  tx_count: number;
}

export interface PlayerNetworkItem {
  player_id: string;
  shared_by: Array<{ type: string; value: string }>;
}

export interface CaseAlertHistory {
  player_id: string;
  cases: Array<{ id: string; title: string; status: string; severity: string; created_at: string }>;
  alerts: Array<{ id: string; title: string; severity: string; status: string; created_at: string }>;
}

export const fetchPlayerTransactionsChart = (playerId: string, days = 90) =>
  api.get<{ player_id: string; days: number; data: TransactionChartItem[] }>(
    `/players/${playerId}/transactions-chart`,
    { params: { days } },
  ).then((r) => r.data);

export const fetchPlayerBetsChart = (playerId: string, days = 90) =>
  api.get<{ player_id: string; days: number; data: BetChartItem[] }>(
    `/players/${playerId}/bets-chart`,
    { params: { days } },
  ).then((r) => r.data);

export const fetchPlayerPaymentInstruments = (playerId: string) =>
  api.get<{ player_id: string; instruments: PaymentInstrumentSummary[] }>(
    `/players/${playerId}/payment-instruments`,
  ).then((r) => r.data);

export const fetchPlayerNetwork = (playerId: string) =>
  api.get<{ player_id: string; related_players: PlayerNetworkItem[] }>(
    `/players/${playerId}/network`,
  ).then((r) => r.data);

export const fetchPlayerCaseAlertHistory = (playerId: string) =>
  api.get<CaseAlertHistory>(`/players/${playerId}/case-alert-history`).then((r) => r.data);

export const addCaseComment = (caseId: string, body: { content: string; mentions?: string[] }) =>
  api.post<{ id: string; created_at: string }>(`/cases/${caseId}/comments`, body).then((r) => r.data);

export const linkAlertToCase = (caseId: string, alertId: string) =>
  api.post<{ case_id: string; alert_id: string; status: string }>(
    `/cases/${caseId}/link-alert`,
    { alert_id: alertId },
  ).then((r) => r.data);

export const updateCaseStatus = (caseId: string, newStatus: string) =>
  api.post<{ id: string; event_type: string; created_at: string }>(
    `/cases/${caseId}/events`,
    { event_type: 'STATUS_CHANGE', content: { new_status: newStatus } },
  ).then((r) => r.data);

// ── Module 7 — Observabilidade e Operação ─────────────────────────────────────

export interface ServiceHealth {
  postgres: string;
  redis: string;
  kafka: string;
  minio: string;
  clickhouse: string;
  ml_service: string;
}

export interface HealthStatus {
  status: 'ok' | 'degraded';
  checks: ServiceHealth;
  timestamp: string;
}

export const fetchHealthStatus = () =>
  api.get<HealthStatus>('/health/ready').then((r) => r.data);

export interface SystemFlag {
  key: string;
  value: Record<string, unknown>;
  updated_at: string | null;
}

export const fetchSystemFlags = () =>
  api.get<SystemFlag[]>('/admin/flags').then((r) => r.data);

export const toggleMaintenanceMode = (enabled: boolean) =>
  api
    .post<{ maintenance_mode: boolean }>(`/admin/maintenance-mode?enabled=${enabled}`)
    .then((r) => r.data);

export const fetchAmlKpis = () =>
  api.get<Record<string, unknown>>('/admin/kpis/aml').then((r) => r.data);

export const designateChallenger = (modelId: string) =>
  api
    .post<{ status: string; model_id: string }>(`/model-registry/${modelId}/challenger`)
    .then((r) => r.data);

// ── Module 8 — Usage stats + invites ─────────────────────────────────────────

export interface UsageStats {
  tenant_id: string;
  period: string;
  events_this_month: number;
  alerts_this_month: number;
  open_cases: number;
  db_size_mb: number;
  minio_mb: number;
}

export const fetchUsageStats = () =>
  api.get<UsageStats>('/admin/stats/usage').then((r) => r.data);

export interface InviteResponse {
  invite_link: string;
  email: string;
  role: string;
  expires_in_hours: number;
}

export const generateInviteLink = (body: { email: string; role: string }) =>
  api.post<InviteResponse>('/admin/invite', body).then((r) => r.data);

