import axios, { type InternalAxiosRequestConfig, type AxiosResponse } from 'axios';
import { getAccessToken, getRefreshToken, storeTokens, clearTokens } from './auth';
import type {
  TokenResponse,
  LoginRequest,
  Alert,
  AlertFilters,
  Case,
  CaseFilters,
  CaseEvent,
  Rule,
  MappingConfig,
  AuditLog,
  AuditLogFilters,
  DashboardStats,
  PaginatedResponse,
  CreateCasePayload,
  AddCaseEventPayload,
  GenerateReportPayload,
  CreateRulePayload,
  SimulateRulePayload,
  SimulateRuleResult,
  CreateMappingConfigPayload,
} from './types';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const api = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
});

// Request interceptor – attach token
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor – handle 401
api.interceptors.response.use(
  (res: AxiosResponse) => res,
  (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      clearTokens();
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  },
);

// ─── Auth ─────────────────────────────────────────────────────────────────────

export async function login(data: LoginRequest): Promise<TokenResponse> {
  // FastAPI OAuth2 expects form data for /token
  const form = new URLSearchParams();
  form.append('username', data.email);
  form.append('password', data.password);
  const res = await api.post<TokenResponse>('/api/v1/auth/token', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  return res.data;
}

export async function refreshToken(): Promise<TokenResponse> {
  const refresh = getRefreshToken();
  const res = await api.post<TokenResponse>('/api/v1/auth/refresh', { refresh_token: refresh });
  storeTokens(res.data.access_token, res.data.refresh_token);
  return res.data;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export async function getDashboardStats(): Promise<DashboardStats> {
  const res = await api.get<DashboardStats>('/api/v1/dashboard/stats');
  return res.data;
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export async function getAlerts(params: AlertFilters = {}): Promise<PaginatedResponse<Alert>> {
  const res = await api.get<PaginatedResponse<Alert>>('/api/v1/alerts', { params });
  return res.data;
}

export async function getAlert(id: string): Promise<Alert> {
  const res = await api.get<Alert>(`/api/v1/alerts/${id}`);
  return res.data;
}

export async function triageAlert(id: string, note: string): Promise<Alert> {
  const res = await api.post<Alert>(`/api/v1/alerts/${id}/triage`, { note });
  return res.data;
}

export async function closeAlert(id: string, verdict: 'TRUE_POSITIVE' | 'FALSE_POSITIVE'): Promise<Alert> {
  const res = await api.post<Alert>(`/api/v1/alerts/${id}/close`, { verdict });
  return res.data;
}

export async function linkAlertToCase(alertId: string, caseId: string): Promise<Alert> {
  const res = await api.post<Alert>(`/api/v1/alerts/${alertId}/link-case`, { case_id: caseId });
  return res.data;
}

// ─── Cases ────────────────────────────────────────────────────────────────────

export async function getCases(params: CaseFilters = {}): Promise<PaginatedResponse<Case>> {
  const res = await api.get<PaginatedResponse<Case>>('/api/v1/cases', { params });
  return res.data;
}

export async function getCase(id: string): Promise<Case> {
  const res = await api.get<Case>(`/api/v1/cases/${id}`);
  return res.data;
}

export async function createCase(data: CreateCasePayload): Promise<Case> {
  const res = await api.post<Case>('/api/v1/cases', data);
  return res.data;
}

export async function addCaseEvent(caseId: string, data: AddCaseEventPayload): Promise<CaseEvent> {
  const res = await api.post<CaseEvent>(`/api/v1/cases/${caseId}/events`, data);
  return res.data;
}

export async function generateReportPackage(caseId: string, data: GenerateReportPayload): Promise<Blob> {
  const res = await api.post(`/api/v1/cases/${caseId}/report`, data, { responseType: 'blob' });
  return res.data as Blob;
}

export async function uploadEvidence(caseId: string, file: File): Promise<CaseEvent> {
  const form = new FormData();
  form.append('file', file);
  const res = await api.post<CaseEvent>(`/api/v1/cases/${caseId}/evidence`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
}

export async function assignCase(caseId: string, userId: string): Promise<Case> {
  const res = await api.post<Case>(`/api/v1/cases/${caseId}/assign`, { user_id: userId });
  return res.data;
}

// ─── Rules ────────────────────────────────────────────────────────────────────

export async function getRules(): Promise<Rule[]> {
  const res = await api.get<Rule[]>('/api/v1/rules');
  return res.data;
}

export async function createRule(data: CreateRulePayload): Promise<Rule> {
  const res = await api.post<Rule>('/api/v1/rules', data);
  return res.data;
}

export async function updateRule(id: string, data: Partial<CreateRulePayload>): Promise<Rule> {
  const res = await api.patch<Rule>(`/api/v1/rules/${id}`, data);
  return res.data;
}

export async function deleteRule(id: string): Promise<void> {
  await api.delete(`/api/v1/rules/${id}`);
}

export async function simulateRule(id: string, payload: SimulateRulePayload): Promise<SimulateRuleResult> {
  const res = await api.post<SimulateRuleResult>(`/api/v1/rules/${id}/simulate`, payload);
  return res.data;
}

// ─── Mapping Configs ──────────────────────────────────────────────────────────

export async function getMappingConfigs(): Promise<MappingConfig[]> {
  const res = await api.get<MappingConfig[]>('/api/v1/mapping-configs');
  return res.data;
}

export async function getMappingConfig(id: string): Promise<MappingConfig> {
  const res = await api.get<MappingConfig>(`/api/v1/mapping-configs/${id}`);
  return res.data;
}

export async function createMappingConfig(data: CreateMappingConfigPayload): Promise<MappingConfig> {
  const res = await api.post<MappingConfig>('/api/v1/mapping-configs', data);
  return res.data;
}

export async function updateMappingConfig(id: string, data: Partial<CreateMappingConfigPayload>): Promise<MappingConfig> {
  const res = await api.patch<MappingConfig>(`/api/v1/mapping-configs/${id}`, data);
  return res.data;
}

export async function deleteMappingConfig(id: string): Promise<void> {
  await api.delete(`/api/v1/mapping-configs/${id}`);
}

// ─── Audit Logs ───────────────────────────────────────────────────────────────

export async function getAuditLogs(params: AuditLogFilters = {}): Promise<PaginatedResponse<AuditLog>> {
  const res = await api.get<PaginatedResponse<AuditLog>>('/api/v1/audit-logs', { params });
  return res.data;
}

export default api;
