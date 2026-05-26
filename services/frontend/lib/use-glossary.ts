/**
 * lib/use-glossary.ts — Hook para usar traduções do glossário
 *
 * Combina o sistema de i18n (useT) com o glossário de termos técnicos.
 * Facilita tradução de enums e termos backend para linguagem operacional.
 *
 * Uso:
 *   const { t, translate } = useGlossary()
 *
 *   // Tradução de strings i18n normais
 *   <h1>{t('dashboard.title')}</h1>
 *
 *   // Tradução de enums backend
 *   <Badge>{translate.severity(alert.severity)}</Badge>
 *   <span>{translate.alertType(alert.alert_type)}</span>
 *   <span>{translate.caseDecision(case.decision)}</span>
 */
'use client';

import { useT } from '@/lib/i18n';
import {
  translateSeverity,
  translateAlertType,
  translateAlertStatus,
  translateCaseStatus,
  translateCaseDecision,
  translateCOSStatus,
  translateFilingChannel,
  translateRiskBand,
  translateGameType,
  translateNotificationType,
  translateIngestMode,
  getSeverityColor,
  getRiskBandColor,
} from '@/lib/glossary';

/**
 * Hook principal para tradução e glossário
 */
export function useGlossary() {
  const t = useT();

  return {
    // i18n padrão
    t,

    // Traduções de enums backend
    translate: {
      severity: translateSeverity,
      alertType: translateAlertType,
      alertStatus: translateAlertStatus,
      caseStatus: translateCaseStatus,
      caseDecision: translateCaseDecision,
      cosStatus: translateCOSStatus,
      filingChannel: translateFilingChannel,
      riskBand: translateRiskBand,
      gameType: translateGameType,
      notificationType: translateNotificationType,
      ingestMode: translateIngestMode,
    },

    // Utilitários de estilo
    style: {
      severityColor: getSeverityColor,
      riskBandColor: getRiskBandColor,
    },
  };
}

/**
 * Hook simplificado apenas para traduções de enum
 * Útil quando não precisa de i18n completo
 */
export function useTranslateEnum() {
  return {
    severity: translateSeverity,
    alertType: translateAlertType,
    alertStatus: translateAlertStatus,
    caseStatus: translateCaseStatus,
    caseDecision: translateCaseDecision,
    cosStatus: translateCOSStatus,
    filingChannel: translateFilingChannel,
    riskBand: translateRiskBand,
    gameType: translateGameType,
    notificationType: translateNotificationType,
    ingestMode: translateIngestMode,
  };
}
