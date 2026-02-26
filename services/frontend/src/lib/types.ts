// ─── Enums ───────────────────────────────────────────────────────────────────

export type UserRole = 'ADMIN' | 'AML_ANALYST' | 'AUDITOR' | 'COMPLIANCE_OFFICER';

export type AlertSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type AlertStatus = 'OPEN' | 'TRIAGED' | 'CLOSED_TP' | 'CLOSED_FP';

export type CaseStatus =
  | 'OPEN'
  | 'UNDER_REVIEW'
  | 'PENDING_COMPLIANCE'
  | 'CLOSED_SUBSTANTIATED'
  | 'CLOSED_UNSUBSTANTIATED';

export type CaseEventType =
  | 'NOTE'
  | 'STATUS_CHANGE'
  | 'ALERT_LINKED'
  | 'EVIDENCE_UPLOADED'
  | 'REPORT_GENERATED'
  | 'ASSIGNMENT_CHANGE';

export type RuleSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
export type RuleScope = 'PLAYER' | 'TRANSACTION' | 'SESSION' | 'AGGREGATE';

// ─── Core entities ────────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface Alert {
  id: string;
  player_id: string;
  player_cpf?: string;
  rule_id: string;
  rule_name: string;
  severity: AlertSeverity;
  status: AlertStatus;
  anomaly_score?: number;
  features?: Record<string, number>;
  thresholds?: Record<string, number>;
  raw_event?: Record<string, unknown>;
  triage_note?: string;
  triaged_by?: string;
  triaged_at?: string;
  verdict?: string;
  closed_by?: string;
  closed_at?: string;
  case_id?: string;
  created_at: string;
  updated_at: string;
}

export interface Case {
  id: string;
  title: string;
  description?: string;
  player_id: string;
  player_cpf?: string;
  status: CaseStatus;
  assigned_to?: string;
  assigned_to_name?: string;
  created_by: string;
  created_by_name?: string;
  created_at: string;
  updated_at: string;
  alerts?: Alert[];
  events?: CaseEvent[];
}

export interface CaseEvent {
  id: string;
  case_id: string;
  event_type: CaseEventType;
  content: string;
  metadata?: Record<string, unknown>;
  created_by: string;
  created_by_name?: string;
  created_at: string;
}

export interface Rule {
  id: string;
  name: string;
  description?: string;
  scope: RuleScope;
  severity: RuleSeverity;
  condition_dsl: string;
  params?: Record<string, unknown>;
  is_active: boolean;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface MappingConfig {
  id: string;
  source_system: string;
  entity_type: string;
  version: string;
  field_mappings: Record<string, string>;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AuditLog {
  id: string;
  actor_id: string;
  actor_email: string;
  action: string;
  resource_type: string;
  resource_id: string;
  details?: Record<string, unknown>;
  ip_address?: string;
  created_at: string;
}

// ─── API helpers ──────────────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

// ─── Dashboard ────────────────────────────────────────────────────────────────

export interface DashboardStats {
  total_alerts: number;
  open_cases: number;
  high_critical_alerts: number;
  active_rules: number;
  alerts_by_day: { date: string; count: number }[];
  top_risk_players: { player_id: string; risk_score: number }[];
  recent_alerts: Alert[];
}

// ─── Filter params ────────────────────────────────────────────────────────────

export interface AlertFilters {
  severity?: AlertSeverity;
  status?: AlertStatus;
  player_id?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export interface CaseFilters {
  status?: CaseStatus;
  assigned_to?: string;
  page?: number;
  page_size?: number;
}

export interface AuditLogFilters {
  actor_id?: string;
  resource_type?: string;
  page?: number;
  page_size?: number;
}

// ─── Form payloads ────────────────────────────────────────────────────────────

export interface CreateCasePayload {
  title: string;
  description?: string;
  player_id: string;
  alert_ids?: string[];
}

export interface AddCaseEventPayload {
  event_type: CaseEventType;
  content: string;
  metadata?: Record<string, unknown>;
}

export interface GenerateReportPayload {
  justification: string;
  include_alert_ids?: string[];
}

export interface CreateRulePayload {
  name: string;
  description?: string;
  scope: RuleScope;
  severity: RuleSeverity;
  condition_dsl: string;
  params?: Record<string, unknown>;
}

export interface SimulateRulePayload {
  test_event: Record<string, unknown>;
}

export interface SimulateRuleResult {
  matched: boolean;
  score?: number;
  details?: Record<string, unknown>;
}

export interface CreateMappingConfigPayload {
  source_system: string;
  entity_type: string;
  version: string;
  field_mappings: Record<string, string>;
}
