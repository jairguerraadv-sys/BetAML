import axios from 'axios';

const BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export const api = axios.create({ baseURL: BASE });

// Injeta token em toda requisição
api.interceptors.request.use((cfg) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('betaml_token');
    if (token) cfg.headers.Authorization = `Bearer ${token}`;
  }
  return cfg;
});

// Redireciona para login em 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && typeof window !== 'undefined') {
      localStorage.removeItem('betaml_token');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  },
);

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function login(username: string, password: string) {
  const form = new URLSearchParams({ username, password });
  const { data } = await api.post('/auth/login', form, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  return data as { access_token: string; token_type: string; user: User };
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
}

export interface Case {
  id: string; reference_number: string; title: string; status: string;
  assigned_to?: string; created_at: string; priority: string;
}

export interface Player {
  id: string; external_id: string; full_name: string; pep_flag: boolean;
  status: string; risk_score?: number;
}

export interface Rule {
  id: string; name: string; description?: string; scope: string;
  condition_dsl: string; severity: string; status: string; version: number;
}

// ── Resources ─────────────────────────────────────────────────────────────────

export const fetchAlerts = (params?: Record<string, string>) =>
  api.get<Alert[]>('/alerts', { params }).then((r) => r.data);

export const fetchCases = (params?: Record<string, string>) =>
  api.get<Case[]>('/cases', { params }).then((r) => r.data);

export const fetchCase = (id: string) =>
  api.get<Case & { events: object[] }>(`/cases/${id}`).then((r) => r.data);

export const fetchPlayers = (params?: Record<string, string>) =>
  api.get<Player[]>('/players', { params }).then((r) => r.data);

export const fetchRules = () => api.get<Rule[]>('/rules').then((r) => r.data);

export const simulateRule = (id: string, payload: object) =>
  api.post<{ matched: boolean; detail: string }>(`/rules/${id}/simulate`, payload).then((r) => r.data);

export const triageAlert = (alertId: string, disposition: string, note: string) =>
  api.post(`/alerts/${alertId}/triage`, { disposition, note }).then((r) => r.data);

export const linkAlertToCase = (alertId: string, caseId: string) =>
  api.post(`/alerts/${alertId}/link-to-case`, { case_id: caseId }).then((r) => r.data);
