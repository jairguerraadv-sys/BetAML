/**
 * lib/i18n.ts — Lightweight i18n for BetAML (no external deps).
 *
 * Usage:
 *   import { useT } from '@/lib/i18n'
 *   const t = useT()
 *   <h1>{t('dashboard.title')}</h1>
 *
 * Add new locales to TRANSLATIONS below.
 * Default locale is pt-BR; user preference stored in localStorage key 'betaml_locale'.
 */
'use client';

import { useEffect, useState, useCallback } from 'react';

export type Locale = 'pt-BR' | 'en-US';
export const LOCALES: Locale[] = ['pt-BR', 'en-US'];

const LOCALE_KEY = 'betaml_locale';
const DEFAULT_LOCALE: Locale = 'pt-BR';

// ── Translation dictionary ────────────────────────────────────────────────────

const TRANSLATIONS: Record<Locale, Record<string, string>> = {
  'pt-BR': {
    // Global
    'app.name': 'BetAML',
    'app.tagline': 'PLD/FT Intelligence',
    'btn.save': 'Salvar',
    'btn.cancel': 'Cancelar',
    'btn.confirm': 'Confirmar',
    'btn.back': 'Voltar',
    'btn.next': 'Próximo',
    'btn.finish': 'Concluir',
    'btn.skip': 'Pular',
    'btn.create': 'Criar',
    'btn.edit': 'Editar',
    'btn.delete': 'Excluir',
    'btn.search': 'Buscar',
    'btn.export': 'Exportar',
    'btn.loading': 'Carregando…',
    'label.yes': 'Sim',
    'label.no': 'Não',
    'label.active': 'Ativo',
    'label.inactive': 'Inativo',
    'label.status': 'Status',
    'label.actions': 'Ações',
    'label.date': 'Data',
    'label.created_at': 'Criado em',
    'label.updated_at': 'Atualizado em',

    // Nav
    'nav.dashboard': 'Painel Diário',
    'nav.alerts': 'Monitor de Alertas',
    'nav.cases': 'Casos em Investigação',
    'nav.sensitivity': 'Ajustes de Sensibilidade',
    'nav.reports': 'Relatórios Reguladores',
    'nav.notifications': 'Notificações',
    'nav.rules_builder': 'Construtor de Regras',
    'nav.rules': 'Condições de Risco',
    'nav.rules_compound': 'Regras Compostas',
    'nav.players': 'Perfis de Clientes',
    'nav.player_lists': 'Listas de Monitoramento',
    'nav.model_registry': 'Modelos Analíticos',
    'nav.feature_store': 'Base de Indicadores',
    'nav.mappings': 'Conectores',
    'nav.audit_logs': 'Log de Auditoria',
    'nav.settings': 'Parâmetros de Sistema',
    'nav.admin': 'Administração',
    'nav.signout': 'Sair da conta',
    'nav.dark_mode': 'Modo escuro',
    'nav.light_mode': 'Modo claro',
    'nav.search_placeholder': 'Buscar…',

    // Dashboard
    'dashboard.title': 'Bom dia',
    'dashboard.kpi.alerts_today': 'Alertas hoje',
    'dashboard.kpi.critical_open': 'Críticos abertos',
    'dashboard.kpi.cases_open': 'Casos em andamento',
    'dashboard.kpi.sla_expired': 'SLA vencido',
    'dashboard.kpi.auto_detected': 'Auto-detectados',
    'dashboard.chart.alerts_by_severity': 'Alertas abertos por prioridade',
    'dashboard.section.critical_recent': 'Alertas críticos recentes',
    'dashboard.section.sla_warning': 'Casos próximos de SLA',
    'dashboard.empty.no_critical': 'Nenhum alerta crítico aberto.',
    'dashboard.empty.no_sla': 'Nenhum caso próximo de SLA.',

    // Alerts
    'alerts.title': 'Monitor de Alertas',
    'alerts.table.severity': 'Severidade',
    'alerts.table.type': 'Tipo',
    'alerts.table.player': 'Jogador',
    'alerts.table.score': 'Score',
    'alerts.table.status': 'Status',
    'alerts.label.true_positive': 'Verdadeiro Positivo',
    'alerts.label.false_positive': 'Falso Positivo',
    'alerts.label.unknown': 'Não classificado',

    // Cases
    'cases.title': 'Casos em Investigação',
    'cases.status.open': 'Aberto',
    'cases.status.investigating': 'Investigando',
    'cases.status.pending_review': 'Aguardando revisão',
    'cases.status.closed': 'Encerrado',
    'cases.status.reported': 'Comunicado ao COAF',
    'cases.table.reference': 'Referência',
    'cases.table.priority': 'Prioridade',
    'cases.table.assigned': 'Responsável',
    'cases.table.sla_due': 'Prazo SLA',

    // Players
    'players.title': 'Perfis de Clientes',
    'players.table.cpf': 'CPF',
    'players.table.risk_band': 'Faixa de Risco',
    'players.table.score': 'Risk Score',
    'players.table.pep': 'PEP',
    'players.erased': 'Registro anonimizado (LGPD)',

    // Severity
    'severity.critical': 'Crítico',
    'severity.high': 'Alto',
    'severity.medium': 'Médio',
    'severity.low': 'Baixo',

    // Errors
    'error.unauthorized': 'Não autorizado. Faça login novamente.',
    'error.not_found': 'Recurso não encontrado.',
    'error.server': 'Erro interno do servidor.',
    'error.generic': 'Algo deu errado. Tente novamente.',
  },

  'en-US': {
    // Global
    'app.name': 'BetAML',
    'app.tagline': 'AML/CFT Intelligence',
    'btn.save': 'Save',
    'btn.cancel': 'Cancel',
    'btn.confirm': 'Confirm',
    'btn.back': 'Back',
    'btn.next': 'Next',
    'btn.finish': 'Finish',
    'btn.skip': 'Skip',
    'btn.create': 'Create',
    'btn.edit': 'Edit',
    'btn.delete': 'Delete',
    'btn.search': 'Search',
    'btn.export': 'Export',
    'btn.loading': 'Loading…',
    'label.yes': 'Yes',
    'label.no': 'No',
    'label.active': 'Active',
    'label.inactive': 'Inactive',
    'label.status': 'Status',
    'label.actions': 'Actions',
    'label.date': 'Date',
    'label.created_at': 'Created at',
    'label.updated_at': 'Updated at',

    // Nav
    'nav.dashboard': 'Daily Dashboard',
    'nav.alerts': 'Alert Monitor',
    'nav.cases': 'Investigations',
    'nav.sensitivity': 'Sensitivity Settings',
    'nav.reports': 'Regulatory Reports',
    'nav.notifications': 'Notifications',
    'nav.rules_builder': 'Rule Builder',
    'nav.rules': 'Risk Rules',
    'nav.rules_compound': 'Compound Rules',
    'nav.players': 'Customer Profiles',
    'nav.player_lists': 'Watch Lists',
    'nav.model_registry': 'ML Models',
    'nav.feature_store': 'Feature Store',
    'nav.mappings': 'Connectors',
    'nav.audit_logs': 'Audit Log',
    'nav.settings': 'System Settings',
    'nav.admin': 'Administration',
    'nav.signout': 'Sign out',
    'nav.dark_mode': 'Dark mode',
    'nav.light_mode': 'Light mode',
    'nav.search_placeholder': 'Search…',

    // Dashboard
    'dashboard.title': 'Good morning',
    'dashboard.kpi.alerts_today': 'Alerts today',
    'dashboard.kpi.critical_open': 'Critical open',
    'dashboard.kpi.cases_open': 'Cases in progress',
    'dashboard.kpi.sla_expired': 'SLA expired',
    'dashboard.kpi.auto_detected': 'Auto-detected',
    'dashboard.chart.alerts_by_severity': 'Open alerts by severity',
    'dashboard.section.critical_recent': 'Recent critical alerts',
    'dashboard.section.sla_warning': 'Cases near SLA deadline',
    'dashboard.empty.no_critical': 'No open critical alerts.',
    'dashboard.empty.no_sla': 'No cases near SLA deadline.',

    // Alerts
    'alerts.title': 'Alert Monitor',
    'alerts.table.severity': 'Severity',
    'alerts.table.type': 'Type',
    'alerts.table.player': 'Player',
    'alerts.table.score': 'Score',
    'alerts.table.status': 'Status',
    'alerts.label.true_positive': 'True Positive',
    'alerts.label.false_positive': 'False Positive',
    'alerts.label.unknown': 'Unknown',

    // Cases
    'cases.title': 'Investigations',
    'cases.status.open': 'Open',
    'cases.status.investigating': 'Investigating',
    'cases.status.pending_review': 'Pending Review',
    'cases.status.closed': 'Closed',
    'cases.status.reported': 'Reported to COAF',
    'cases.table.reference': 'Reference',
    'cases.table.priority': 'Priority',
    'cases.table.assigned': 'Assigned to',
    'cases.table.sla_due': 'SLA Deadline',

    // Players
    'players.title': 'Customer Profiles',
    'players.table.cpf': 'Tax ID',
    'players.table.risk_band': 'Risk Band',
    'players.table.score': 'Risk Score',
    'players.table.pep': 'PEP',
    'players.erased': 'Anonymised record (LGPD)',

    // Severity
    'severity.critical': 'Critical',
    'severity.high': 'High',
    'severity.medium': 'Medium',
    'severity.low': 'Low',

    // Errors
    'error.unauthorized': 'Unauthorized. Please log in again.',
    'error.not_found': 'Resource not found.',
    'error.server': 'Internal server error.',
    'error.generic': 'Something went wrong. Please try again.',
  },
};

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useLocale(): [Locale, (l: Locale) => void] {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    const saved = localStorage.getItem(LOCALE_KEY) as Locale | null;
    if (saved && LOCALES.includes(saved)) setLocaleState(saved);
  }, []);

  const setLocale = useCallback((l: Locale) => {
    localStorage.setItem(LOCALE_KEY, l);
    setLocaleState(l);
  }, []);

  return [locale, setLocale];
}

export function useT(overrideLocale?: Locale) {
  const [locale] = useLocale();
  const active = overrideLocale ?? locale;

  return useCallback(
    (key: string, fallback?: string): string => {
      return (
        TRANSLATIONS[active]?.[key] ??
        TRANSLATIONS[DEFAULT_LOCALE]?.[key] ??
        fallback ??
        key
      );
    },
    [active],
  );
}

/** Locale-aware date formatter */
export function fmtDate(iso: string | null | undefined, locale: Locale = DEFAULT_LOCALE): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat(locale, {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

/** Locale-aware currency formatter */
export function fmtCurrency(value: number | null | undefined, locale: Locale = DEFAULT_LOCALE): string {
  if (value == null) return '—';
  const currency = locale === 'en-US' ? 'USD' : 'BRL';
  return new Intl.NumberFormat(locale, { style: 'currency', currency }).format(value);
}
