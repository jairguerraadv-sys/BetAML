import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { format, parseISO } from 'date-fns';
import type { AlertSeverity, AlertStatus, CaseStatus, UserRole } from './types';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatDate(iso: string, fmt = 'dd/MM/yyyy HH:mm'): string {
  try {
    return format(parseISO(iso), fmt);
  } catch {
    return iso;
  }
}

/** Mask CPF to ***.***.***-XX (last 2 digits visible) */
export function maskCpf(cpf: string): string {
  const digits = cpf.replace(/\D/g, '');
  if (digits.length !== 11) return '***.***.***-**';
  return `***.***.***-${digits.slice(9)}`;
}

/** Return full formatted CPF: 000.000.000-00 */
export function formatCpf(cpf: string): string {
  const d = cpf.replace(/\D/g, '');
  if (d.length !== 11) return cpf;
  return `${d.slice(0, 3)}.${d.slice(3, 6)}.${d.slice(6, 9)}-${d.slice(9)}`;
}

/** Truncate UUID-style IDs for display */
export function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

export function severityColor(severity: AlertSeverity): string {
  const map: Record<AlertSeverity, string> = {
    LOW: 'success',
    MEDIUM: 'warning',
    HIGH: 'danger',
    CRITICAL: 'danger',
  };
  return map[severity] ?? 'default';
}

export function alertStatusLabel(status: AlertStatus): string {
  const map: Record<AlertStatus, string> = {
    OPEN: 'Open',
    TRIAGED: 'Triaged',
    CLOSED_TP: 'Closed (TP)',
    CLOSED_FP: 'Closed (FP)',
  };
  return map[status] ?? status;
}

export function caseStatusLabel(status: CaseStatus): string {
  const map: Record<CaseStatus, string> = {
    OPEN: 'Open',
    UNDER_REVIEW: 'Under Review',
    PENDING_COMPLIANCE: 'Pending Compliance',
    CLOSED_SUBSTANTIATED: 'Closed – Substantiated',
    CLOSED_UNSUBSTANTIATED: 'Closed – Unsubstantiated',
  };
  return map[status] ?? status;
}

export function roleLabel(role: UserRole): string {
  const map: Record<UserRole, string> = {
    ADMIN: 'Admin',
    AML_ANALYST: 'AML Analyst',
    AUDITOR: 'Auditor',
    COMPLIANCE_OFFICER: 'Compliance',
  };
  return map[role] ?? role;
}

export function canSeeFull(role: UserRole): boolean {
  return role === 'ADMIN' || role === 'AML_ANALYST';
}
