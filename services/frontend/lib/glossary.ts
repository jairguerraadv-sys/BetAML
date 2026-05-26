/**
 * lib/glossary.ts — Glossário centralizado BetAML
 *
 * Tradução de termos técnicos do backend para linguagem operacional brasileira.
 * Fonte de verdade única para mapeamento backend → frontend.
 *
 * PRINCÍPIO: Zero jargão técnico na UI padrão.
 * - Enums traduzidos para português claro
 * - SAR → COS (contexto brasileiro)
 * - Linguagem operacional, não de engenharia
 */

// ═══════════════════════════════════════════════════════════════════════════════
// SEVERIDADE DE ALERTAS E CASOS
// ═══════════════════════════════════════════════════════════════════════════════

export const SEVERITY_LABELS: Record<string, string> = {
  CRITICAL: 'Crítica',
  HIGH: 'Alta',
  MEDIUM: 'Média',
  LOW: 'Baixa',
};

export const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  HIGH: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
  MEDIUM: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
  LOW: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
};

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS DE ALERTAS
// ═══════════════════════════════════════════════════════════════════════════════

export const ALERT_STATUS_LABELS: Record<string, string> = {
  OPEN: 'Aberto',
  IN_PROGRESS: 'Em análise',
  UNDER_REVIEW: 'Em revisão',
  TRIAGED: 'Triado',
  CLOSED: 'Encerrado',
  ESCALATED: 'Escalado',
  DISMISSED: 'Arquivado',
};

// ═══════════════════════════════════════════════════════════════════════════════
// ORIGEM DO ALERTA (alert_type)
// ═══════════════════════════════════════════════════════════════════════════════

export const ALERT_TYPE_LABELS: Record<string, string> = {
  // Principais tipos
  RULE: 'Regra automática',
  ANOMALY: 'Modelo de anomalia',
  ML_ANOMALY: 'Inteligência artificial',
  COMPOSITE: 'Score composto',
  NETWORK: 'Análise de rede',

  // Tipos específicos (mantidos do pld-language.ts)
  VELOCITY: 'Movimentação em velocidade incompatível',
  STRUCTURING: 'Possível fracionamento de valores',
  PEP_EXPOSURE: 'Exposição a PEP ou jurisdição de risco',
  MULTI_ACCOUNT: 'Uso incomum de contas ou dispositivos',
  HIGH_RISK_CUST: 'Cliente com perfil de risco elevado',
  SLOT_FREQUENCY: 'Frequência atípica em slots',
  CASINO_WASHING: 'Padrão atípico em casino ao vivo',
  PRODUCT_DIVERSITY: 'Uso incomum de várias modalidades',

  // Novos tipos (do backend)
  INCOME_INCOMPATIBILITY: 'Incompatibilidade patrimonial',
  AML_SUSPICIOUS: 'Atividade suspeita de PLD',
  AML_HIGH_RISK: 'Alto risco de PLD',

  // COAF
  COAF_DEADLINE_WARNING: 'Prazo Coaf próximo',
  COAF_DEADLINE_BREACH: 'Prazo Coaf vencido',
};

export const ALERT_TYPE_DESCRIPTIONS: Record<string, string> = {
  RULE: 'Alerta gerado por condição de risco configurada',
  ANOMALY: 'Comportamento detectado como fora do padrão histórico',
  ML_ANOMALY: 'Padrão suspeito identificado por inteligência artificial',
  COMPOSITE: 'Combinação de múltiplos sinais de risco',
  NETWORK: 'Vínculos suspeitos com outros jogadores detectados',
};

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS DE CASOS
// ═══════════════════════════════════════════════════════════════════════════════

export const CASE_STATUS_LABELS: Record<string, string> = {
  OPEN: 'Aberto',
  INVESTIGATING: 'Em investigação',
  IN_PROGRESS: 'Em andamento',
  PENDING_REVIEW: 'Aguardando revisão',
  PENDING_APPROVAL: 'Aguardando aprovação',
  UNDER_REVIEW: 'Em revisão',
  CLOSED: 'Encerrado',
  RESOLVED: 'Resolvido',
  REPORTED: 'Comunicado ao Coaf',
  ARCHIVED: 'Arquivado',
};

// ═══════════════════════════════════════════════════════════════════════════════
// DECISÃO DE CASO → COMUNICAÇÃO AO COAF (COS)
// ═══════════════════════════════════════════════════════════════════════════════
//
// ⚠️ IMPORTANTE: Contexto Brasileiro de PLD/FT
//
// No backend, o enum mantém "FILE_SAR" por compatibilidade técnica e internacional.
// Na UI brasileira, SEMPRE exiba "COS" (Comunicação de Operação Suspeita).
//
// Motivo: O Coaf brasileiro define oficialmente como "COS", não "SAR/STR".
// - SAR (Suspicious Activity Report) = termo americano
// - STR (Suspicious Transaction Report) = termo FATF/internacional
// - COS (Comunicação de Operação Suspeita) = termo brasileiro oficial
//
// A Lei 9.613/98 e as circulares do Coaf usam "comunicação de operação suspeita".
//
// ═══════════════════════════════════════════════════════════════════════════════

export const CASE_DECISION_LABELS: Record<string, string> = {
  // ⚠️ MUDANÇA CRÍTICA: SAR → COS
  FILE_SAR: 'Comunicar ao Coaf',
  FILE_COS: 'Comunicar ao Coaf', // Novo padrão
  REPORT: 'Reportar internamente',
  NO_ACTION: 'Arquivar sem comunicar',
  MONITOR: 'Manter em monitoramento',
  ESCALATE: 'Escalar para supervisor',
  FALSE_POSITIVE: 'Falso positivo',
};

export const CASE_DECISION_DESCRIPTIONS: Record<string, string> = {
  FILE_SAR: 'Preparar Comunicação de Operação Suspeita (COS) para envio ao Coaf',
  FILE_COS: 'Preparar Comunicação de Operação Suspeita (COS) para envio ao Coaf',
  REPORT: 'Gerar relatório interno sem comunicação externa',
  NO_ACTION: 'Arquivar caso sem comunicação ao regulador',
  MONITOR: 'Manter jogador em lista de monitoramento ativo',
  ESCALATE: 'Requer aprovação ou análise de nível superior',
  FALSE_POSITIVE: 'Alerta incorreto, não procede investigação',
};

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS DE COMUNICAÇÃO AO COAF (Report Package / COS)
// ═══════════════════════════════════════════════════════════════════════════════

export const COS_STATUS_LABELS: Record<string, string> = {
  // Status de preparação
  DRAFT: 'Rascunho',
  IN_PREPARATION: 'Em preparação',
  PENDING_APPROVAL: 'Aguardando aprovação',
  READY_TO_FILE: 'Pronta para enviar',

  // Status de envio (Filing)
  FILED: 'Comunicada ao Coaf',
  SUBMITTED: 'Enviada',
  PENDING_PROTOCOL: 'Aguardando protocolo',

  // Status finais
  COMPLETED: 'Concluída',
  ARCHIVED: 'Arquivada',
};

export const COS_FILING_CHANNEL_LABELS: Record<string, string> = {
  MANUAL_PORTAL: 'Enviada manualmente no portal do Coaf',
  AUTOMATED: 'Enviada via integração automática',
  API: 'Enviada via API',
  BULK_UPLOAD: 'Enviada em lote',
  NOT_APPLICABLE: 'Não se aplica',
};

// ═══════════════════════════════════════════════════════════════════════════════
// DISPOSIÇÃO DE TRIAGEM
// ═══════════════════════════════════════════════════════════════════════════════

export const TRIAGE_DISPOSITION_LABELS: Record<string, string> = {
  TRUE_POSITIVE: 'Positivo verdadeiro',
  FALSE_POSITIVE: 'Falso positivo',
  ESCALATE_TO_CASE: 'Abrir caso',
  REQUIRES_REVIEW: 'Requer revisão',
  UNKNOWN: 'Não classificado',
  PENDING: 'Pendente',
};

// ═══════════════════════════════════════════════════════════════════════════════
// FAIXA DE RISCO (Risk Band)
// ═══════════════════════════════════════════════════════════════════════════════

export const RISK_BAND_LABELS: Record<string, string> = {
  CRITICAL: 'Risco crítico',
  HIGH: 'Risco alto',
  MEDIUM: 'Risco médio',
  LOW: 'Risco baixo',
  MINIMAL: 'Risco mínimo',
  UNKNOWN: 'Risco não avaliado',
};

export const RISK_BAND_COLORS: Record<string, string> = {
  CRITICAL: 'bg-red-600 text-white',
  HIGH: 'bg-orange-500 text-white',
  MEDIUM: 'bg-yellow-500 text-white',
  LOW: 'bg-green-500 text-white',
  MINIMAL: 'bg-blue-500 text-white',
  UNKNOWN: 'bg-gray-400 text-white',
};

// ═══════════════════════════════════════════════════════════════════════════════
// TIPOS DE JOGO (Game Types)
// ═══════════════════════════════════════════════════════════════════════════════

export const GAME_TYPE_LABELS: Record<string, string> = {
  SPORTSBOOK: 'Apostas esportivas',
  CASINO_LIVE: 'Cassino ao vivo',
  SLOT: 'Caça-níqueis',
  SLOTS: 'Caça-níqueis',
  BINGO: 'Bingo',
  INSTANT_WIN: 'Jogos instantâneos',
  INSTANT_GAMES: 'Jogos instantâneos',
  SCRATCH_CARD: 'Raspadinha',
  POKER: 'Pôquer',
  TABLE_GAMES: 'Jogos de mesa',
};

// ═══════════════════════════════════════════════════════════════════════════════
// MODO DE INGESTÃO
// ═══════════════════════════════════════════════════════════════════════════════

export const INGEST_MODE_LABELS: Record<string, string> = {
  incremental: 'Carga incremental',
  backfill: 'Carga histórica',
  reprocess: 'Reprocessamento',
  manual: 'Importação manual',
};

// ═══════════════════════════════════════════════════════════════════════════════
// TIPOS DE NOTIFICAÇÃO
// ═══════════════════════════════════════════════════════════════════════════════

export const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  ALERT_CRITICAL: 'Alerta crítico',
  CASE_ASSIGNED: 'Caso atribuído',
  SLA_WARNING: 'Prazo próximo',
  MODEL_DRIFT: 'Degradação de modelo',
  FEATURE_DRIFT: 'Desvio de indicadores',
  DLQ_PENDING: 'Erros de importação pendentes',
  MENTION: 'Menção',
  SYSTEM: 'Notificação do sistema',
  COAF_DEADLINE_WARNING: 'Prazo Coaf próximo',
  COAF_DEADLINE_BREACH: 'Prazo Coaf vencido',
};

// ═══════════════════════════════════════════════════════════════════════════════
// TERMOS GERAIS - VOCABULÁRIO OPERACIONAL
// ═══════════════════════════════════════════════════════════════════════════════

export const GENERAL_TERMS: Record<string, string> = {
  // Entidades
  alert: 'Alerta',
  case: 'Caso',
  player: 'Jogador',
  user: 'Usuário',
  tenant: 'Operador',

  // Ações
  create: 'Criar',
  edit: 'Editar',
  delete: 'Excluir',
  archive: 'Arquivar',
  assign: 'Atribuir',
  escalate: 'Escalar',
  approve: 'Aprovar',
  reject: 'Rejeitar',
  submit: 'Enviar',
  file: 'Protocolar',

  // Status
  active: 'Ativo',
  inactive: 'Inativo',
  enabled: 'Habilitado',
  disabled: 'Desabilitado',
  pending: 'Pendente',
  completed: 'Concluído',

  // Tempo
  created_at: 'Criado em',
  updated_at: 'Atualizado em',
  created_via: 'Origem',

  // Comunicação Coaf
  sar: 'COS', // ⚠️ MUDANÇA CRÍTICA
  str: 'COS', // ⚠️ MUDANÇA CRÍTICA
  report_package: 'Dossiê',
  filing: 'Comunicação',
  protocol: 'Protocolo',

  // Técnico → Operacional
  marker: 'Sinalizador',
  evidence: 'Evidências',
  score: 'Pontuação',
  threshold: 'Limite',
  sensitivity: 'Sensibilidade',
  rule: 'Regra',
  condition: 'Condição',
  trigger: 'Acionamento',
};

// ═══════════════════════════════════════════════════════════════════════════════
// FUNÇÕES UTILITÁRIAS DE TRADUÇÃO
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Traduz severidade de enum backend para label operacional
 */
export function translateSeverity(severity?: string | null): string {
  if (!severity) return 'Não definida';
  return SEVERITY_LABELS[severity.toUpperCase()] ?? severity;
}

/**
 * Traduz tipo de alerta de enum backend para label operacional
 */
export function translateAlertType(alertType?: string | null): string {
  if (!alertType) return 'Sinal de risco';
  return ALERT_TYPE_LABELS[alertType.toUpperCase()] ?? alertType.replaceAll('_', ' ').toLowerCase();
}

/**
 * Traduz status de alerta de enum backend para label operacional
 */
export function translateAlertStatus(status?: string | null): string {
  if (!status) return 'Sem status';
  return ALERT_STATUS_LABELS[status.toUpperCase()] ?? status;
}

/**
 * Traduz status de caso de enum backend para label operacional
 */
export function translateCaseStatus(status?: string | null): string {
  if (!status) return 'Sem status';
  return CASE_STATUS_LABELS[status.toUpperCase()] ?? status;
}

/**
 * Traduz decisão de caso de enum backend para label operacional
 * ⚠️ IMPORTANTE: Converte SAR → COS automaticamente
 */
export function translateCaseDecision(decision?: string | null): string {
  if (!decision) return 'Sem decisão';

  // Normalizar FILE_SAR → FILE_COS
  const normalized = decision.toUpperCase().replace('FILE_SAR', 'FILE_COS');

  return CASE_DECISION_LABELS[normalized] ?? CASE_DECISION_LABELS[decision.toUpperCase()] ?? decision;
}

/**
 * Traduz status de COS/Report Package
 */
export function translateCOSStatus(status?: string | null): string {
  if (!status) return 'Sem status';

  // Normalizar variações
  if (status.toUpperCase() === 'FILED') return 'Comunicada ao Coaf';
  if (status.toUpperCase() === 'PENDING') return 'Pendente';

  return COS_STATUS_LABELS[status.toUpperCase()] ?? status;
}

/**
 * Traduz canal de filing
 */
export function translateFilingChannel(channel?: string | null): string {
  if (!channel) return 'Não especificado';
  return COS_FILING_CHANNEL_LABELS[channel.toUpperCase()] ?? channel;
}

/**
 * Traduz faixa de risco
 */
export function translateRiskBand(riskBand?: string | null): string {
  if (!riskBand) return 'Não avaliado';
  return RISK_BAND_LABELS[riskBand.toUpperCase()] ?? riskBand;
}

/**
 * Traduz tipo de jogo
 */
export function translateGameType(gameType?: string | null): string {
  if (!gameType) return 'Não especificado';
  return GAME_TYPE_LABELS[gameType.toUpperCase()] ?? gameType;
}

/**
 * Traduz tipo de notificação
 */
export function translateNotificationType(type?: string | null): string {
  if (!type) return 'Notificação';
  return NOTIFICATION_TYPE_LABELS[type.toUpperCase()] ?? type;
}

/**
 * Traduz modo de ingestão
 */
export function translateIngestMode(mode?: string | null): string {
  if (!mode) return 'Padrão';
  return INGEST_MODE_LABELS[mode.toLowerCase()] ?? mode;
}

/**
 * Obtém cor de severidade para Tailwind classes
 */
export function getSeverityColor(severity?: string | null): string {
  if (!severity) return 'bg-gray-100 text-gray-800';
  return SEVERITY_COLORS[severity.toUpperCase()] ?? 'bg-gray-100 text-gray-800';
}

/**
 * Obtém cor de faixa de risco para Tailwind classes
 */
export function getRiskBandColor(riskBand?: string | null): string {
  if (!riskBand) return 'bg-gray-400 text-white';
  return RISK_BAND_COLORS[riskBand.toUpperCase()] ?? 'bg-gray-400 text-white';
}

// ═══════════════════════════════════════════════════════════════════════════════
// EXPORTAÇÃO DE CONSTANTES ÚTEIS
// ═══════════════════════════════════════════════════════════════════════════════

export const GLOSSARY_VERSION = '1.0.0';
export const LAST_UPDATED = '2026-05-26';

/**
 * Mapa completo de termos para busca/referência
 */
export const COMPLETE_GLOSSARY = {
  severity: SEVERITY_LABELS,
  alertStatus: ALERT_STATUS_LABELS,
  alertType: ALERT_TYPE_LABELS,
  caseStatus: CASE_STATUS_LABELS,
  caseDecision: CASE_DECISION_LABELS,
  cosStatus: COS_STATUS_LABELS,
  filingChannel: COS_FILING_CHANNEL_LABELS,
  triageDisposition: TRIAGE_DISPOSITION_LABELS,
  riskBand: RISK_BAND_LABELS,
  gameType: GAME_TYPE_LABELS,
  ingestMode: INGEST_MODE_LABELS,
  notificationType: NOTIFICATION_TYPE_LABELS,
  general: GENERAL_TERMS,
};
