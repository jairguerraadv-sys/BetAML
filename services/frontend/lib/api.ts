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
  id: string; actor_id: string; action: string; entity_type: string;
  entity_id: string; ip_address?: string; created_at: string;
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

export const fetchCases = (params?: Record<string, string>) =>
  api.get<Case[]>('/cases', { params }).then((r) => r.data);

export const fetchCase = (id: string) =>
  api.get<CaseDetail>(`/cases/${id}`).then((r) => r.data);

export const fetchPlayers = (params?: Record<string, string>) =>
  api.get<Player[]>('/players', { params }).then((r) => r.data);

export const fetchPlayer = (id: string) =>
  api.get<PlayerDetail>(`/players/${id}`).then((r) => r.data);

export const fetchPlayerEconCompat = (id: string) =>
  api.get<EconCompat>(`/players/${id}/econ-compat`).then((r) => r.data);

export const fetchAlertRelatedTransactions = (alertId: string) =>
  api.get<RelatedTransactions>(`/alerts/${alertId}/related-transactions`).then((r) => r.data);

export const generateReportPackage = (
  caseId: string,
  body: { analyst_narrative?: string; decision?: string },
) => api.post<ReportPackageResult>(`/cases/${caseId}/report-package`, body).then((r) => r.data);

export interface ScoringConfig {
  id: number;
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

export const fetchRules = () => api.get<Rule[]>('/rules').then((r) => r.data);

export const simulateRule = (id: string, payload: object) =>
  api.post<{ matched: boolean; detail: string }>(`/rules/${id}/simulate`, payload).then((r) => r.data);

export const triageAlert = (alertId: string, disposition: string, note: string) =>
  api.post(`/alerts/${alertId}/triage`, { disposition, note }).then((r) => r.data);

export const linkAlertToCase = (alertId: string, caseId: string) =>
  api.post(`/alerts/${alertId}/link-to-case`, { case_id: caseId }).then((r) => r.data);

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
