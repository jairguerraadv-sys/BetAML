import axios, { AxiosError, InternalAxiosRequestConfig } from 'axios';

// Sempre usa o proxy local — o servidor Next.js encaminha para a API.
// Isso garante que localhost:8000 nunca é chamado direto do browser,
// resolvendo ERR_CONNECTION_REFUSED em Codespaces/devcontainer.
const BASE = '/api-proxy';

export const api = axios.create({ baseURL: BASE });

type RetriableRequestConfig = InternalAxiosRequestConfig & { _retry?: boolean };

// O JWT é transportado como cookie httpOnly (setado via /api/auth/login).
// O middleware Next.js (middleware.ts) injeta automaticamente o header
// Authorization: Bearer <token> nas chamadas /api-proxy/*.
// NENHUM código JS no browser tem acesso direto ao token — imune a XSS.

api.interceptors.response.use(
  (r) => r,
  async (err: AxiosError) => {
    const original = err.config as RetriableRequestConfig | undefined;
    if (err.response?.status === 401 && original && !original._retry && typeof window !== 'undefined') {
      original._retry = true;
      try {
        await refreshToken();
        return api.request(original);
      } catch {
        window.location.href = '/login';
      }
    } else if (err.response?.status === 401 && typeof window !== 'undefined') {
      window.location.href = '/login';
    }
    return Promise.reject(err);
  },
);

// ── Auth ──────────────────────────────────────────────────────────────────────

/** Resposta da API route Next.js /api/auth/login (sem o token — fica no cookie). */
export interface LoginResponse {
  role: string;
  roles: string[];
  tenant_id: string;
}

/**
 * Login via Next.js API route que seta cookie httpOnly.
 * NÃO chama o backend diretamente — usa /api/auth/login como proxy server-side.
 */
export async function login(username: string, password: string, tenantSlug?: string): Promise<LoginResponse> {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, tenant_slug: tenantSlug?.trim() || undefined }),
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
  const res = await fetch('/api/auth/refresh', { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? 'Sessão expirada');
  }
  return res.json() as Promise<LoginResponse>;
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

export interface AuditLogFilters {
  page?: string;
  per_page?: string;
  entity_type?: string;
  entity_id?: string;
  action?: string;
  user_id?: string;
  actor_id?: string;
  date_from?: string;
  date_to?: string;
  q?: string;
  pii_only?: string;
}

export const fetchAuditLogs = (params?: AuditLogFilters) =>
  api.get<AuditLog[]>('/audit-logs', { params }).then((r) => r.data);

export interface MonthlyReportAlertsBySeverity {
  CRITICAL: number;
  HIGH: number;
  MEDIUM: number;
  LOW: number;
}

export interface MonthlyReportTopRule {
  rule_id: string;
  rule_name: string;
  fires: number;
}

export interface MonthlyReportTopPlayer {
  player_id: string;
  external_id: string;
  avg_risk_score: number;
}

export interface MonthlyReportQualityMetrics {
  labeled_alerts: number;
  true_positive_count: number;
  false_positive_count: number;
  unknown_count: number;
  true_positive_rate: number | null;
  false_positive_rate: number | null;
}

export interface MonthlyReport {
  period: { from: string; to: string };
  alerts_by_severity: MonthlyReportAlertsBySeverity;
  total_alerts: number;
  cases_summary: Record<string, number>;
  total_cases: number;
  total_cases_opened: number;
  total_cases_closed: number;
  total_cases_reported: number;
  top_rules_by_fires: MonthlyReportTopRule[];
  top_players_by_risk: MonthlyReportTopPlayer[];
  total_ingested_events: number;
  total_communications_generated: number;
  false_positive_rate: number | null;
  total_sar_reports: number;
  true_positive_rate: number | null;
  quality_metrics: MonthlyReportQualityMetrics;
  generated_at: string;
}

export const fetchMonthlySummary = (dateFrom: string, dateTo: string) =>
  api.get<MonthlyReport>('/reports/monthly-summary', {
    params: { date_from: dateFrom, date_to: dateTo },
  }).then((r) => r.data);

// ── Types ─────────────────────────────────────────────────────────────────────

export interface User {
  id: string; email: string; username: string; role: string; tenant_id: string;
}

export interface Alert {
  id: string; title: string; severity: string; status: string;
  player_id: string; alert_type: string; created_at: string; rule_id?: string;
  anomaly_score?: number; case_id?: string; case_reference_number?: string | null;
  game_type?: string;
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
  label_note?: string;
  labeled_at?: string;
  case_status?: string | null;
  case_title?: string | null;
}

export interface AlertExplainabilityFeature {
  feature: string;
  current_value: unknown;
  baseline_value?: number | null;
  delta?: number | null;
  contribution: number;
}

export interface AlertExplainability {
  alert_id: string;
  model_id?: string | null;
  explanation_method: string;
  explanation_method_actual?: string;
  anomaly_score: number;
  top_features: AlertExplainabilityFeature[];
}

export interface ScoreResponse {
  player_id: string;
  tenant_id: string;
  model_id?: string | null;
  anomaly_score: number;
  risk_band: string;
  model_variant?: string;
  scored_at: string;
}

export interface Case {
  id: string; reference_number: string; title: string; status: string;
  assigned_to?: string; created_at: string; priority: string;
  severity?: string; player_id?: string; auto_created?: boolean;
  sla_due_at?: string;
}

export interface CaseEvidenceFile {
  event_id: string;
  file_name?: string | null;
  description?: string | null;
  content_type?: string | null;
  size_bytes: number;
  sha256?: string | null;
  storage_backend?: string | null;
  uploaded_at?: string | null;
  download_path: string;
}

export interface CaseDetail extends Case {
  description?: string;
  alerts: Array<{ id: string; severity: string; title: string }>;
  timeline: Array<{ id: string; event_type: string; content: Record<string, unknown>; created_at: string }>;
  evidence_files: CaseEvidenceFile[];
  report_packages?: Array<{
    id: string;
    status: string;
    format: string;
    decision: string | null;
    created_at: string;
    generated_by?: string | null;
    pdf_available: boolean;
  }>;
}

export interface Player {
  id: string;
  external_player_id: string;
  cpf_masked: string;
  pep_flag: boolean;
  risk_score: number;
  risk_band: 'LOW' | 'MEDIUM' | 'HIGH';
  status: string;
  self_exclusion_flag: boolean;
  deposit_limit_daily: number | null;
  created_at: string;
}

export interface PlayerDetail {
  id: string; external_player_id: string; cpf: string; pep_flag: boolean;
  risk_score: number; risk_band: 'LOW' | 'MEDIUM' | 'HIGH';
  declared_income_monthly: number | null; last_scored_at: string | null;
  status: string;
  self_exclusion_flag: boolean;
  deposit_limit_daily: number | null;
}

export interface KycEvent {
  id: string;
  event_type: string;
  provider: string;
  status: string;
  error_message: string | null;
  processed_at: string | null;
  created_at: string;
}

export interface PlayerErasureResponse {
  status: string;
  player_id: string;
  message: string;
  erased_at?: string;
  erased_from?: Record<string, number>;
}

export interface PlayerDataExport {
  export_id: string;
  generated_at: string;
  player_id: string;
  personal_data: {
    name?: string | null;
    cpf?: string | null;
    birth_date?: string | null;
    email?: string | null;
    pep_flag: boolean;
    registered_since?: string | null;
  };
  financial_summary: {
    total_transactions: number;
    total_deposits: number;
    total_withdrawals: number;
    first_transaction?: string | null;
    last_transaction?: string | null;
  };
  cases_count: number;
  alerts_count: number;
}

export interface FeatureStoreCurrent {
  player_id: string;
  source: string;
  feature_version: number;
  snapshot_version: number;
  entity_type: string;
  snapshot_date?: string;
  gold_object_path?: string | null;
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
  entity_type: string;
  gold_object_path?: string | null;
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

export interface FeatureQualityFinding {
  feature_name: string;
  finding_type: string;
  current_value: number;
  previous_value?: number | null;
  delta?: number | null;
  severity: string;
}

export interface FeatureQualityStatus {
  feature_date: string | null;
  previous_feature_date: string | null;
  drift_detected: boolean;
  max_drift_score: number;
  admin_notification_sent: boolean;
  findings: FeatureQualityFinding[];
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

// ── COAF Siscoaf 97 — Tabelas de Ocorrência e Envolvimento (Portaria SPA/MF 1.143/2024) ──
export const SISCOAF_OCCURRENCE_CODES: Record<number, string> = {
  1407: 'Art. 24-I — Falta de fundamento econômico ou legal',
  1408: 'Art. 24-II — Incompatibilidade com práticas usuais de mercado',
  1409: 'Art. 24-III — Possível indício de lavagem de dinheiro ou financiamento ao terrorismo',
  1410: 'Art. 25-I — Pessoa envolvida em LD ou crimes financeiros',
  1411: 'Art. 25-II — Terrorismo / proliferação de armas',
  1412: 'Art. 25-III — Jurisdição GAFI de alto risco ou sob monitoramento',
  1413: 'Art. 25-IV — Resistência a fornecer informações cadastrais',
  1414: 'Art. 25-V — Informações falsas ou de difícil verificação',
  1415: 'Art. 25-VI — Aporte suspeito quanto à origem dos recursos',
  1416: 'Art. 25-VII — Prêmio suspeito de ser instrumento de LD/FTP/fraude',
  1417: 'Art. 25-VIII — Manipulação de resultados',
  1418: 'Art. 25-IX — Incompatibilidade comportamental com o perfil',
  1419: 'Art. 25-X — Utilização de ferramenta automatizada (bots)',
  1420: 'Art. 25-XI — Fracionamento / dissimulação de operações',
  1421: 'Art. 25-XII — Retirada imediata pós-depósito sem apostas',
  1422: 'Art. 25-XIII — Utilização indevida de conta de terceiro',
  1423: 'Art. 25-XIV — Agente intermediador de apostas',
  1424: 'Art. 25-XV — Aportes sugestivos de intermediação de apostas',
  1425: 'Art. 25-XVI — Uso de plataforma bet exchange para LD/FTP',
  1426: 'Art. 25-XVII — Pessoa Politicamente Exposta (PEP)',
  1427: 'Art. 25-XVIII — Dificuldade de realização cadastral',
  1428: 'Art. 25-XIX — Qualquer operação com características atípicas (catch-all)',
};

export const SISCOAF_INVOLVEMENT_TYPES: Record<number, string> = {
  1:  'Titular',
  8:  'Outros',
  49: 'Apostador',
  50: 'Usuário de Plataforma',
  51: 'Jogador de Casino/Slots',
};

export interface ReportPackageBody {
  analyst_narrative?: string;
  decision?: 'FILE_SAR' | 'NO_ACTION' | 'PENDING';
  /** Códigos de ocorrência Siscoaf (1407–1428). Obrigatório para decision=FILE_SAR. */
  occurrence_codes?: number[];
  /** Tipos de envolvimento: 1=Titular, 8=Outros, 49=Apostador, 50=Usuário de Plataforma */
  involvement_types?: number[];
  /** Valor do prêmio recebido (R$) */
  valor_premio?: number;
  /** Valor total das apostas no período (R$) */
  valor_apostas?: number;
  /** Informações adicionais obrigatórias para todos os códigos de ocorrência (não pode ser nulo) */
  informacoes_adicionais?: string;
}

export interface ReportPackageResult {
  report_package_id: string;
  status: string;
  decision: string;
  pdf_path: string | null;
  payload: Record<string, unknown>;
  integrity_hash?: string;
}

export interface CaseLookupResult {
  alerts: Array<{ id: string; title: string; severity: string; created_at: string }>;
  transactions: Array<{ id: string; type: string; amount: number; status: string; occurred_at: string }>;
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
  weight?: number;
}

// ── Resources ─────────────────────────────────────────────────────────────────

export const fetchAlerts = (params?: Record<string, string>) =>
  api.get<{ total: number; items: Alert[] }>('/alerts', { params }).then((r) => r.data);

export const fetchAlert = (id: string) =>
  api.get<AlertDetail>(`/alerts/${id}`).then((r) => r.data);

export const fetchAlertExplainability = (id: string) =>
  api.get<AlertExplainability>(`/alerts/${id}/explainability`).then((r) => r.data);

export const fetchCases = (params?: Record<string, string | number>) =>
  api.get<Case[]>('/cases', { params }).then((r) => r.data);

export const fetchCase = (id: string) =>
  api.get<CaseDetail>(`/cases/${id}`).then((r) => r.data);

export const uploadCaseEvidence = (
  caseId: string,
  body: { file: File; description?: string },
) => {
  const formData = new FormData();
  formData.append('file', body.file);
  if (body.description?.trim()) {
    formData.append('description', body.description.trim());
  }
  return api.post<CaseEvidenceFile>(`/cases/${caseId}/evidence`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data);
};

export const createCase = (body: {
  title: string;
  description?: string;
  player_id?: string;
  severity?: string;
}) => api.post<{ id: string; title: string; status: string; reference_number?: string }>('/cases', body).then((r) => r.data);

export const fetchPlayers = (params?: Record<string, string>) =>
  api.get<Player[]>('/players', { params }).then((r) => r.data);

export const fetchPlayer = (id: string) =>
  api.get<PlayerDetail>(`/players/${id}`).then((r) => r.data);

export const erasePlayerData = (playerId: string, reason?: string) =>
  api.post<PlayerErasureResponse>(`/players/${playerId}/erase`, null, {
    params: reason ? { reason } : undefined,
  }).then((r) => r.data);

export const requestPlayerRightToErasure = (playerId: string, reason?: string) =>
  api.post<PlayerErasureResponse>(`/players/${playerId}/right-to-erasure`, null, {
    params: reason ? { reason } : undefined,
  }).then((r) => r.data);

export const fetchPlayerDataExport = (playerId: string) =>
  api.get<PlayerDataExport>(`/players/${playerId}/data-export`).then((r) => r.data);

export const fetchFeatureStoreCurrent = (playerId: string) =>
  api.get<FeatureStoreCurrent>(`/feature-store/players/${playerId}/current`).then((r) => r.data);

export const fetchFeatureStoreHistory = (
  playerId: string,
  params?: { from?: string; to?: string },
) =>
  api.get<FeatureStoreHistory>(`/feature-store/players/${playerId}/history`, { params }).then((r) => r.data);

export const fetchFeaturePopulationStats = () =>
  api.get<FeaturePopulationStats>('/feature-store/population-stats').then((r) => r.data);

export const fetchFeatureQualityLatest = () =>
  api.get<FeatureQualityStatus>('/feature-store/quality/latest').then((r) => r.data);

export const fetchPlayerEconCompat = (id: string) =>
  api.get<EconCompat>(`/players/${id}/econ-compat`).then((r) => r.data);

export const fetchAlertRelatedTransactions = (alertId: string) =>
  api.get<RelatedTransactions>(`/alerts/${alertId}/related-transactions`).then((r) => r.data);

export const generateReportPackage = (
  caseId: string,
  body: ReportPackageBody,
) => api.post<ReportPackageResult>(`/cases/${caseId}/report-package`, body).then((r) => r.data);

export interface CaseNarrativeSuggestion {
  case_id: string;
  suggested_narrative: string;
  alerts_considered: number;
  player: Record<string, unknown>;
}

export const fetchCaseNarrativeSuggestion = (caseId: string) =>
  api.get<CaseNarrativeSuggestion>(`/cases/${caseId}/report-package/narrative-suggest`).then((r) => r.data);

export interface ReportPackageMeta {
  id: string;
  status: string;
  format: string;
  decision: string | null;
  created_at: string;
  generated_by?: string | null;
  pdf_available: boolean;
  xml_path?: string | null;
  xml_sha256?: string | null;
  coaf_protocol_number?: string | null;
  filed_at?: string | null;
  integrity_hash?: string;
}

export interface ReportFilingStatus {
  case_id: string;
  report_package_id: string | null;
  report_status: string | null;
  report_decision: string | null;
  requires_submission: boolean;
  protocol_registered: boolean;
  coaf_protocol_number: string | null;
  filing_channel: string;
  days_since_report_created: number | null;
  days_since_filed: number | null;
  deadline_state: 'NO_REPORT' | 'OK' | 'WARNING' | 'BREACH' | string;
  warnings: string[];
}

export interface ReportFilingOverview {
  total_cases_with_reports: number;
  requires_submission_count: number;
  missing_protocol_count: number;
  deadline_state_counts: Record<string, number>;
  oldest_pending_submission_days: number | null;
  top_breach_case_ids: string[];
  truncated: boolean;
}

export interface ReportFilingHotlistItem {
  case_id: string;
  report_package_id: string;
  report_status: string;
  deadline_state: string;
  action_required: 'SUBMIT_REPORT' | 'REGISTER_PROTOCOL' | string;
  priority_rank: number;
  requires_submission: boolean;
  protocol_registered: boolean;
  days_since_report_created: number | null;
  warnings: string[];
}

export interface ReportFilingHotlist {
  total_items: number;
  items: ReportFilingHotlistItem[];
}

export interface ReportFilingQueueItem {
  case_id: string;
  report_package_id: string;
  report_status: string;
  report_decision: string | null;
  requires_submission: boolean;
  protocol_registered: boolean;
  coaf_protocol_number: string | null;
  days_since_report_created: number | null;
  days_since_filed: number | null;
  deadline_state: string;
  warnings: string[];
}

export interface ReportFilingQueue {
  total_items: number;
  deadline_state_counts: Record<string, number>;
  items: ReportFilingQueueItem[];
}

export interface SubmitReportResult {
  status: string;
  report_package_id: string;
  tracking_id: string;
  submitted_at: string;
  submitted_by: string;
  channel: string;
  xml_path: string | null;
  xml_sha256: string | null;
  message: string;
}

export const fetchCaseReportPackages = (caseId: string) =>
  api.get<ReportPackageMeta[]>(`/cases/${caseId}/report-packages`).then((r) => r.data);

export const fetchReportFilingStatus = (caseId: string) =>
  api.get<ReportFilingStatus>(`/cases/${caseId}/report-filing-status`).then((r) => r.data);

export const fetchReportFilingOverview = () =>
  api.get<ReportFilingOverview>('/report-packages/filing-overview').then((r) => r.data);

export const fetchReportFilingHotlist = (limit = 20) =>
  api.get<ReportFilingHotlist>('/report-packages/filing-hotlist', { params: { limit } }).then((r) => r.data);

export const fetchReportFilingQueue = (limit = 50) =>
  api.get<ReportFilingQueue>('/report-packages/filing-queue', { params: { limit } }).then((r) => r.data);

export const submitReportPackage = (caseId: string) =>
  api.post<SubmitReportResult>(`/cases/${caseId}/report-package/submit`).then((r) => r.data);

export const registerCoafProtocol = (caseId: string, rpId: string, coafProtocolNumber: string) =>
  api.patch<{ report_package_id: string; coaf_protocol_number: string; registered_at: string }>(
    `/cases/${caseId}/report-packages/${rpId}/protocol-number`,
    { coaf_protocol_number: coafProtocolNumber },
  ).then((r) => r.data);

export const downloadReportPackage = (rpId: string) =>
  api.get(`/report-packages/${rpId}/download`, { responseType: 'blob' }).then((r) => r.data as Blob);

export const exportReportPackageHtml = (rpId: string) =>
  api.get(`/report-packages/${rpId}/export`, { responseType: 'blob' }).then((r) => r.data as Blob);

export const submitReportPackageFiling = (rpId: string) =>
  api.post<{ report_package_id: string; status: string; filed_at: string; channel: string; message: string }>(
    `/report-packages/${rpId}/submit-filing`,
  ).then((r) => r.data);

export const assignCase = (caseId: string, userId: string) =>
  api.post(`/cases/${caseId}/assign`, { user_id: userId }).then((r) => r.data);

export const lookupCaseEntities = (caseId: string, q: string, scope: 'all' | 'alerts' | 'transactions' = 'all') =>
  api.get<CaseLookupResult>(`/cases/${caseId}/lookup`, { params: { q, scope } }).then((r) => r.data);

export const linkTransactionToCase = (caseId: string, transactionId: string) =>
  api.post(`/cases/${caseId}/link-transaction`, { transaction_id: transactionId }).then((r) => r.data);

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
  auto_case_threshold: number;
  risk_band_low_threshold: number;
  risk_band_high_threshold: number;
  income_volume_ratio_threshold: number;
  ingest_rate_limit_tpm: number;
  ml_challenger_pct?: number;
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
  weight?: number;
}

export const fetchRules = () => api.get<Rule[]>('/rules').then((r) => r.data);

export const createRule = (body: RuleCreatePayload) =>
  api.post<Rule>('/rules', body).then((r) => r.data);

export const validateDsl = (condition_dsl: string) =>
  api.post<{ valid: boolean; message?: string }>('/rules/validate', { expression: condition_dsl }).then((r) => r.data);

export interface SimulateRuleResult {
  rule_id: string;
  results: Array<{ matched: boolean; event: Record<string, unknown>; error?: string }>;
  matches: number;
  total_alerts?: number;
  players?: string[];
  false_positive_estimated?: number | null;
  precision_estimated?: number | null;
  recall_estimated?: number | null;
  performance_score?: number | null;
  timeline?: Array<{ date: string; alerts: number }>;
}

export const simulateRule = (id: string, payload: object) =>
  api.post<SimulateRuleResult>(`/rules/${id}/simulate`, payload).then((r) => r.data);

export const previewDsl = (condition_dsl: string, severity: string, scope: string, days = 30) =>
  api
    .post<SimulateRuleResult & { evaluated: number; errors: number; days: number }>(
      '/rules/preview-dsl',
      { condition_dsl, severity, scope, days },
    )
    .then((r) => r.data);

export interface RuleMacro {
  id: string;
  tenant_id: string;
  name: string;
  expression: string;
  description?: string | null;
  created_at: string;
}

export interface CompoundRule {
  id: string;
  tenant_id: string;
  name: string;
  logic?: string | null;
  component_rule_ids: string[];
  score_weights: Record<string, number>;
  min_score_threshold?: number | null;
  is_active: boolean;
  created_at: string;
}

export interface PlayerList {
  id: string;
  tenant_id?: string | null;
  name: string;
  description?: string | null;
  list_type: string;
  source?: string | null;
  active?: boolean;
  entry_count: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PlayerListEntry {
  id: string;
  value?: string | null;
  value_type?: string | null;
  external_player_id?: string | null;
  cpf_hash?: string | null;
  notes?: string | null;
  added_at?: string | null;
}

export const ingestFile = (formData: FormData) =>
  api
    .post<{ status: string; rows_processed?: number }>('/ingest/file', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    .then((r) => r.data);

export type AlertTriageDisposition = 'IN_REVIEW' | 'CONFIRMED' | 'FALSE_POSITIVE' | 'DISMISSED';

export const triageAlert = (alertId: string, disposition: AlertTriageDisposition, note: string) =>
  api.post(`/alerts/${alertId}/triage`, { disposition, note }).then((r) => r.data);

export const closeAlert = (alertId: string) =>
  api.post<{ id: string; status: string }>(`/alerts/${alertId}/close`).then((r) => r.data);

export const labelAlert = (
  alertId: string,
  label: 'TRUE_POSITIVE' | 'FALSE_POSITIVE' | 'NEED_REVIEW',
  labelNote?: string,
) =>
  api.post<{ status: string; label: string }>(`/alerts/${alertId}/label`, {
    label,
    label_note: labelNote,
  }).then((r) => r.data);

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
  canonical_validation?: {
    entity_type: string;
    valid: boolean;
    missing_required_groups?: string[][];
    unknown_targets?: string[];
    unknown_fields?: string[];
    empty_required_fields?: string[];
    allowed_fields?: string[];
  };
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
  canonical_validation?: MappingDetail['canonical_validation'];
}

export interface MappingPreviewResponse extends MappingValidateResponse {
  preview?: Record<string, unknown>;
  sample_parse?: {
    accepted: number;
    failed: number;
    total: number;
    errors: Array<Record<string, unknown>>;
  } | null;
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

export interface MappingCreateResult {
  id: string;
  name: string;
  version_number: number;
  is_current: boolean;
}

export interface MappingTestResponse {
  status: 'ok' | 'error';
  canonical?: Record<string, unknown>;
  detail?: string;
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
  sample?: Record<string, unknown>;
  sample_text?: string;
  format: 'json' | 'yaml';
}) => api.post<MappingPreviewResponse>('/mappings/preview', body).then((r) => r.data);

export const testMapping = (id: string, body: { sample: Record<string, unknown> }) =>
  api.post<MappingTestResponse>(`/mappings/${id}/test`, body).then((r) => r.data);

export const createMapping = (body: MappingCreatePayload) =>
  api.post<MappingCreateResult>('/mappings', body).then((r) => r.data);

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
  message?: string | null;
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

// ── Self-exclusão e KYC de players ─────────────────────────────────────────

export const setSelfExclusion = (playerId: string, reason?: string) =>
  api.post<{ player_id: string; self_exclusion_flag: boolean; status: string }>(
    `/players/${playerId}/self-exclusion`,
    reason ? { reason } : {},
  ).then((r) => r.data);

export const clearSelfExclusion = (playerId: string) =>
  api.delete<{ player_id: string; self_exclusion_flag: boolean; status: string }>(
    `/players/${playerId}/self-exclusion`,
  ).then((r) => r.data);

export const updateDepositLimit = (playerId: string, depositLimitDaily: number) =>
  api.patch<{ player_id: string; deposit_limit_daily: number }>(
    `/players/${playerId}/deposit-limit`,
    { deposit_limit_daily: depositLimitDaily },
  ).then((r) => r.data);

export const createKycEvent = (
  playerId: string,
  body: { event_type: string; provider?: string; status?: string; payload?: Record<string, unknown> },
) =>
  api.post<KycEvent & { player_status: string }>(
    `/players/${playerId}/kyc-events`,
    body,
  ).then((r) => r.data);

export const fetchKycEvents = (playerId: string) =>
  api.get<KycEvent[]>(`/players/${playerId}/kyc-events`).then((r) => r.data);

// ── Model Registry ──────────────────────────────────────────────────────────

export interface ModelRegistry {
  id: string;
  tenant_id: string;
  model_name: string;
  model_type: string;
  algorithm?: string | null;
  version: string;
  dataset_window_start?: string | null;
  dataset_window_end?: string | null;
  dataset_window_days?: number | null;
  sample_count?: number | null;
  training_rows: number | null;
  feature_columns: string[];
  metrics: Record<string, unknown>;
  artifact_path?: string | null;
  artifact_uri?: string | null;
  status: string;
  is_challenger: boolean;
  promoted_by: string | null;
  promoted_at: string | null;
  trained_by: string | null;
  trained_at: string | null;
  created_at: string;
}

export interface ModelABTimelinePoint {
  date: string;
  champion_inferences: number;
  challenger_inferences: number;
  champion_avg_score?: number | null;
  challenger_avg_score?: number | null;
  champion_tp: number;
  champion_fp: number;
  challenger_tp: number;
  challenger_fp: number;
}

export interface ModelABMetrics {
  model_id: string;
  model_name?: string | null;
  role: string;
  status: string;
  days_window: number;
  champion_model_id?: string | null;
  challenger_model_id?: string | null;
  champion_inferences: number;
  challenger_inferences: number;
  champion_avg_score?: number | null;
  challenger_avg_score?: number | null;
  champion_precision_estimated?: number | null;
  challenger_precision_estimated?: number | null;
  champion_recall_estimated?: number | null;
  challenger_recall_estimated?: number | null;
  champion_false_positive_rate?: number | null;
  challenger_false_positive_rate?: number | null;
  timeline: ModelABTimelinePoint[];
}

export interface ModelPerformanceTotals {
  total_alerts: number;
  labeled_alerts: number;
  true_positive_count: number;
  false_positive_count: number;
  unknown_count: number;
  precision_estimated: number;
  false_positive_rate: number;
  recall_estimated: number;
}

export interface ModelPerformancePoint {
  date: string;
  total_alerts: number;
  true_positive_count: number;
  false_positive_count: number;
  unknown_count: number;
}

export interface RulePerformanceItem {
  rule_id?: string | null;
  rule_name: string;
  total_alerts: number;
  true_positive_count: number;
  false_positive_count: number;
  unknown_count: number;
  precision_estimated: number;
  false_positive_rate: number;
}

export interface ModelPerformanceItem {
  model_id: string;
  model_name?: string | null;
  algorithm?: string | null;
  status: string;
  total_alerts: number;
  true_positive_count: number;
  false_positive_count: number;
  unknown_count: number;
  precision_estimated: number;
  recall_estimated: number;
  false_positive_rate: number;
}

export interface ModelPerformanceSummary {
  days_window: number;
  challenger_split_pct: number;
  totals: ModelPerformanceTotals;
  by_day: ModelPerformancePoint[];
  by_rule: RulePerformanceItem[];
  by_model: ModelPerformanceItem[];
}

export const fetchModelRegistry = (modelType?: string) =>
  api.get<ModelRegistry[]>('/model-registry', { params: modelType ? { model_type: modelType } : {} })
    .then((r) => r.data);

export const fetchModelABMetrics = (modelId: string, days = 30) =>
  api.get<ModelABMetrics>(`/model-registry/${modelId}/ab-metrics`, { params: { days } })
    .then((r) => r.data);

export const fetchModelPerformanceSummary = (days = 30) =>
  api.get<ModelPerformanceSummary>('/model-registry/performance/summary', { params: { days } })
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

export interface AdminOnboardingCreateMappingPayload {
  name: string;
  source_system: string;
  entity_type: string;
  config_text?: string;
  config_json?: Record<string, unknown>;
  format: 'json' | 'yaml';
  change_notes?: string;
  version?: string;
}

export interface AdminOnboardingCreateRulePayload {
  name: string;
  description?: string;
  status?: string;
  severity: string;
  scope?: string;
  condition_dsl: string;
  params?: Record<string, unknown>;
  weight?: number;
}

export const adminOnboardingCreateMapping = (tenantId: string, body: AdminOnboardingCreateMappingPayload) =>
  api.post<{ id: string; name: string; version_number: number; is_current: boolean }>(
    `/admin/onboarding/${tenantId}/mappings`,
    body,
  ).then((r) => r.data);

export const adminOnboardingIngestFile = (tenantId: string, formData: FormData) =>
  api.post<{ job_id: string; status: string; source_system: string; file_name?: string }>(
    `/admin/onboarding/${tenantId}/ingest-sample`,
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  ).then((r) => r.data);

export const adminOnboardingCreateRule = (tenantId: string, body: AdminOnboardingCreateRulePayload) =>
  api.post<{ id: string; name: string; status: string }>(
    `/admin/onboarding/${tenantId}/rules`,
    body,
  ).then((r) => r.data);

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
  roles?: string[];
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
  api.post<{ user_id: string; username: string; message: string }>(`/admin/users/${id}/reset-password`, { new_password }).then((r) => r.data);

export interface AdminApiKey {
  id: string;
  tenant_id: string;
  name: string;
  key_prefix: string;
  source_system?: string | null;
  permissions: string[];
  active: boolean;
  last_used_at?: string | null;
  expires_at?: string | null;
  created_at: string;
}

export interface AdminApiKeyCreatePayload {
  name: string;
  source_system?: string;
  permissions?: string[];
  expires_in_days?: number;
}

export interface AdminApiKeyCreateResult extends AdminApiKey {
  raw_key: string;
}

export interface AdminApiKeyUsage {
  key_id: string;
  key_prefix: string;
  name: string;
  source_system?: string | null;
  permissions: string[];
  active: boolean;
  last_used_at?: string | null;
  total_requests_30d: number;
  days: Record<string, number>;
}

export const fetchApiKeys = () =>
  api.get<AdminApiKey[]>('/admin/api-keys').then((r) => r.data);

export const createApiKey = (body: AdminApiKeyCreatePayload) =>
  api.post<AdminApiKeyCreateResult>('/admin/api-keys', body).then((r) => r.data);

export const revokeApiKey = (id: string) =>
  api.delete(`/admin/api-keys/${id}`);

export const fetchApiKeyUsage = (id: string) =>
  api.get<AdminApiKeyUsage>(`/admin/api-keys/${id}/usage`).then((r) => r.data);

// ── Stats ─────────────────────────────────────────────────────────────────────

export interface DashboardStats {
  generated_at?: string;
  alerts_today:  number;
  critical_open: number;
  cases_open:    number;
  sla_expired:   number;
  auto_detected: number;
  by_severity:   Record<string, number>;
  alerts_open: number;
  cases_investigating: number;
  cases_near_sla: number;
  high_risk_players: number;
  events_ingested_today: number;
  alerts_by_severity_30d: Array<{
    date: string;
    CRITICAL: number;
    HIGH: number;
    MEDIUM: number;
    LOW: number;
    total: number;
  }>;
  alerts_by_rule_type: Array<{ label: string; value: number }>;
  top_players_by_risk: Array<{
    player_id: string;
    external_player_id: string;
    risk_score: number;
    risk_band: string;
  }>;
  alert_heatmap: Array<{ weekday: number; hour: number; count: number }>;
  // Analyst-specific KPIs
  dismissed_7d?: number;
  my_cases_near_sla?: number;
  high_fp_rules?: Array<{ rule_id: string; rule_name: string; fp_count: number }>;
  tenant_name?: string;
  tenant_slug?: string;
}

export const fetchDashboardStats = () =>
  api.get<DashboardStats>('/stats/dashboard').then((r) => r.data);

// ── Ingest Jobs & Errors (Módulo 1) ──────────────────────────────────────────

export type IngestJobStatus = 'QUEUED' | 'PROCESSING' | 'DONE' | 'PARTIAL' | 'FAILED';

export interface IngestJob {
  id: string;
  source_system: string;
  file_name: string | null;
  connector_type?: string | null;
  status: IngestJobStatus;
  total_records: number | null;
  processed_records: number | null;
  failed_records: number | null;
  bytes_processed: number;
  duration_ms: number | null;
  mapping_config_id?: string | null;
  mapping_version_id?: string | null;
  error_message?: string | null;
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
  error_sample_preview?: Array<Record<string, unknown>>;
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
    mapping_version_id?: string;
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

// Multi-modalidade (Lei 14.790/2023 art. 3º)
export type ProductType = 'SPORTSBOOK' | 'CASINO_LIVE' | 'SLOT' | 'INSTANT_GAME' | 'BINGO' | 'RASPADINHA' | 'VIRTUAL';

export const PRODUCT_TYPE_LABELS: Record<ProductType, string> = {
  SPORTSBOOK:   'Apostas Esportivas',
  CASINO_LIVE:  'Casino ao Vivo',
  SLOT:         'Slots / Caça-Níqueis',
  INSTANT_GAME: 'Jogos Instantâneos',
  BINGO:        'Bingo Online',
  RASPADINHA:   'Raspadinha Digital',
  VIRTUAL:      'Esportes Virtuais',
};

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

interface PlayerNetworkApiEdge {
  source: string;
  target: string;
  edge_type: string;
  shared_hash_prefix?: string;
}

interface PlayerNetworkApiResponse {
  focal_player_id: string;
  edges: PlayerNetworkApiEdge[];
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
  api.get<PlayerNetworkApiResponse>(`/players/${playerId}/network`).then((r) => {
    const data = r.data;
    const related = new Map<string, Array<{ type: string; value: string }>>();

    for (const edge of data.edges ?? []) {
      let peerId: string | null = null;
      if (edge.source === playerId) peerId = edge.target;
      else if (edge.target === playerId) peerId = edge.source;
      if (!peerId) continue;

      const link = {
        type: edge.edge_type,
        value: edge.shared_hash_prefix ?? 'shared',
      };
      const current = related.get(peerId) ?? [];
      if (!current.some((item) => item.type === link.type && item.value === link.value)) {
        related.set(peerId, [...current, link]);
      }
    }

    return {
      player_id: data.focal_player_id ?? playerId,
      related_players: Array.from(related.entries()).map(([peer_id, shared_by]) => ({
        player_id: peer_id,
        shared_by,
      })),
    };
  });

export const fetchPlayerCaseAlertHistory = (playerId: string) =>
  api.get<CaseAlertHistory>(`/players/${playerId}/case-alert-history`).then((r) => r.data);

export interface ExternalValidationResult {
  request_id: string;
  status: string;
  response: Record<string, unknown>;
  provider?: string;
  requested_at?: string;
  completed_at?: string;
}

export interface ExternalValidationHistoryItem {
  request_id: string;
  provider: string;
  validation_type: string;
  status: string;
  requested_at?: string;
  completed_at?: string;
  error_message?: string | null;
}

export interface ExternalValidationHistory {
  player_id: string;
  limit: number;
  offset: number;
  total: number;
  items: ExternalValidationHistoryItem[];
}

export const requestPlayerExternalValidation = (
  playerId: string,
  body: { provider?: string; validation_type?: string; payload?: Record<string, unknown> } = {},
) =>
  api.post<ExternalValidationResult>(`/players/${playerId}/external-validation`, body).then((r) => r.data);

export const fetchLatestPlayerExternalValidation = (playerId: string) =>
  api.get<ExternalValidationResult>(`/players/${playerId}/external-validation/latest`).then((r) => r.data);

export const fetchPlayerExternalValidationHistory = (
  playerId: string,
  limit = 20,
  offset = 0,
  filters?: { status?: string; provider?: string },
) =>
  api.get<ExternalValidationHistory>(`/players/${playerId}/external-validation/history`, {
    params: { limit, offset, ...(filters ?? {}) },
  }).then((r) => r.data);

export const fetchExternalValidationById = (requestId: string) =>
  api.get<ExternalValidationResult>(`/external-validation/${requestId}`).then((r) => r.data);

export const retryExternalValidation = (requestId: string) =>
  api.post<{ status: string; request_id: string; retries_from: string }>(`/external-validation/${requestId}/retry`).then((r) => r.data);
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
  rules_engine?: string;
  stream_processor?: string;
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
    .put<{ maintenance_mode: boolean }>(`/admin/maintenance-mode?enabled=${enabled}`)
    .then((r) => r.data);

export const fetchAmlKpis = () =>
  api.get<Record<string, unknown>>('/admin/kpis/aml').then((r) => r.data);

export interface OperationalAlert {
  code: string;
  severity: string;
  message: string;
  value?: number | null;
  threshold?: number | null;
}

export interface OpsSummary {
  generated_at: string;
  maintenance_mode: boolean;
  kafka_consumer_lag: number;
  ingest_error_rate_24h_percent: number;
  unresolved_dlq_events: number;
  dlq_breakdown: Array<{
    source_system: string;
    entity_type: string | null;
    count: number;
  }>;
  ingest_rate_limit_per_min: number;
  ws_active_connections: number;
  ws_queued_messages: number;
  ws_peak_queue_depth: number;
  ws_backpressure_events: number;
  ws_last_backpressure_at?: string | null;
  stale_models: number;
  oldest_model_age_days?: number | null;
  alerts: OperationalAlert[];
}

export const fetchOpsSummary = () =>
  api.get<OpsSummary>('/admin/ops/summary').then((r) => r.data);

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
