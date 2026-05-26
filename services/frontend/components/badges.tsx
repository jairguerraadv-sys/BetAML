/**
 * components/badges.tsx — Badges reutilizáveis com glossário centralizado
 *
 * Componentes de badge padronizados para exibição consistente de:
 * - Severidade de alertas/casos
 * - Status de alertas/casos
 * - Faixa de risco de jogadores
 * - Tipos de jogos
 * - Tipos de notificações
 *
 * USO:
 *   import { SeverityBadge, AlertStatusBadge, RiskBandBadge } from '@/components/badges'
 *
 *   <SeverityBadge severity={alert.severity} />
 *   <AlertStatusBadge status={alert.status} />
 *   <RiskBandBadge riskBand={player.risk_band} />
 */
'use client';

import { useGlossary } from '@/lib/use-glossary';
import { cn } from '@/lib/utils';

// ═══════════════════════════════════════════════════════════════════════════════
// SEVERIDADE (Alertas e Casos)
// ═══════════════════════════════════════════════════════════════════════════════

interface SeverityBadgeProps {
  severity: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function SeverityBadge({ severity, size = 'md', className }: SeverityBadgeProps) {
  const { translate, style } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
    lg: 'text-sm px-2.5 py-1',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-semibold',
        style.severityColor(severity),
        sizeClasses[size],
        className
      )}
    >
      {translate.severity(severity)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS DE ALERTAS
// ═══════════════════════════════════════════════════════════════════════════════

interface AlertStatusBadgeProps {
  status: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function AlertStatusBadge({ status, size = 'md', className }: AlertStatusBadgeProps) {
  const { translate } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
    lg: 'text-sm px-2.5 py-1',
  };

  const statusColors: Record<string, string> = {
    OPEN: 'bg-blue-100 text-blue-700',
    IN_PROGRESS: 'bg-indigo-100 text-indigo-700',
    UNDER_REVIEW: 'bg-purple-100 text-purple-700',
    TRIAGED: 'bg-teal-100 text-teal-700',
    CLOSED: 'bg-gray-100 text-gray-500',
    ESCALATED: 'bg-orange-100 text-orange-700',
    DISMISSED: 'bg-gray-200 text-gray-600',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-semibold',
        statusColors[status.toUpperCase()] ?? 'bg-gray-100 text-gray-600',
        sizeClasses[size],
        className
      )}
    >
      {translate.alertStatus(status)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// STATUS DE CASOS
// ═══════════════════════════════════════════════════════════════════════════════

interface CaseStatusBadgeProps {
  status: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function CaseStatusBadge({ status, size = 'md', className }: CaseStatusBadgeProps) {
  const { translate } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
    lg: 'text-sm px-2.5 py-1',
  };

  const statusColors: Record<string, string> = {
    OPEN: 'bg-blue-100 text-blue-700',
    INVESTIGATING: 'bg-indigo-100 text-indigo-700',
    IN_PROGRESS: 'bg-indigo-100 text-indigo-700',
    PENDING_REVIEW: 'bg-purple-100 text-purple-700',
    PENDING_APPROVAL: 'bg-purple-100 text-purple-700',
    UNDER_REVIEW: 'bg-purple-100 text-purple-700',
    CLOSED: 'bg-gray-100 text-gray-500',
    RESOLVED: 'bg-green-100 text-green-700',
    REPORTED: 'bg-green-100 text-green-700',
    ARCHIVED: 'bg-gray-200 text-gray-600',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-semibold',
        statusColors[status.toUpperCase()] ?? 'bg-gray-100 text-gray-600',
        sizeClasses[size],
        className
      )}
    >
      {translate.caseStatus(status)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// FAIXA DE RISCO (Risk Band)
// ═══════════════════════════════════════════════════════════════════════════════

interface RiskBandBadgeProps {
  riskBand: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function RiskBandBadge({ riskBand, size = 'md', className }: RiskBandBadgeProps) {
  const { translate, style } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
    lg: 'text-sm px-2.5 py-1',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-bold',
        style.riskBandColor(riskBand),
        sizeClasses[size],
        className
      )}
    >
      {translate.riskBand(riskBand)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TIPO DE JOGO
// ═══════════════════════════════════════════════════════════════════════════════

interface GameTypeBadgeProps {
  gameType: string;
  size?: 'sm' | 'md';
  className?: string;
}

export function GameTypeBadge({ gameType, size = 'sm', className }: GameTypeBadgeProps) {
  const { translate } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
  };

  const gameColors: Record<string, string> = {
    SPORTSBOOK: 'bg-blue-100 text-blue-700',
    CASINO_LIVE: 'bg-purple-100 text-purple-700',
    SLOT: 'bg-yellow-100 text-yellow-700',
    SLOTS: 'bg-yellow-100 text-yellow-700',
    BINGO: 'bg-green-100 text-green-700',
    INSTANT_WIN: 'bg-pink-100 text-pink-700',
    INSTANT_GAMES: 'bg-pink-100 text-pink-700',
    SCRATCH_CARD: 'bg-orange-100 text-orange-700',
    POKER: 'bg-red-100 text-red-700',
    TABLE_GAMES: 'bg-indigo-100 text-indigo-700',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-semibold',
        gameColors[gameType.toUpperCase()] ?? 'bg-gray-100 text-gray-600',
        sizeClasses[size],
        className
      )}
    >
      {translate.gameType(gameType)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TIPO DE ALERTA (Alert Type / Origem)
// ═══════════════════════════════════════════════════════════════════════════════

interface AlertTypeBadgeProps {
  alertType: string;
  size?: 'sm' | 'md';
  className?: string;
}

export function AlertTypeBadge({ alertType, size = 'sm', className }: AlertTypeBadgeProps) {
  const { translate } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
  };

  const typeColors: Record<string, string> = {
    RULE: 'bg-blue-100 text-blue-700',
    ANOMALY: 'bg-orange-100 text-orange-700',
    ML_ANOMALY: 'bg-purple-100 text-purple-700',
    COMPOSITE: 'bg-indigo-100 text-indigo-700',
    NETWORK: 'bg-teal-100 text-teal-700',
    COAF_DEADLINE_WARNING: 'bg-yellow-100 text-yellow-700',
    COAF_DEADLINE_BREACH: 'bg-red-100 text-red-700',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-medium',
        typeColors[alertType.toUpperCase()] ?? 'bg-gray-100 text-gray-600',
        sizeClasses[size],
        className
      )}
    >
      {translate.alertType(alertType)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// DECISÃO DE CASO (COS)
// ═══════════════════════════════════════════════════════════════════════════════

interface CaseDecisionBadgeProps {
  decision: string;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function CaseDecisionBadge({ decision, size = 'md', className }: CaseDecisionBadgeProps) {
  const { translate } = useGlossary();

  const sizeClasses = {
    sm: 'text-[10px] px-1.5 py-0.5',
    md: 'text-xs px-2 py-0.5',
    lg: 'text-sm px-2.5 py-1',
  };

  const decisionColors: Record<string, string> = {
    FILE_SAR: 'bg-red-100 text-red-700 border border-red-200',
    FILE_COS: 'bg-red-100 text-red-700 border border-red-200',
    REPORT: 'bg-orange-100 text-orange-700',
    NO_ACTION: 'bg-gray-100 text-gray-600',
    MONITOR: 'bg-blue-100 text-blue-700',
    ESCALATE: 'bg-purple-100 text-purple-700',
    FALSE_POSITIVE: 'bg-green-100 text-green-700',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center rounded font-semibold',
        decisionColors[decision.toUpperCase()] ?? 'bg-gray-100 text-gray-600',
        sizeClasses[size],
        className
      )}
    >
      {translate.caseDecision(decision)}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// UTILITÁRIO: cn() para merge de classes Tailwind
// ═══════════════════════════════════════════════════════════════════════════════
// Se não existir lib/utils.ts com cn(), descomente abaixo:
/*
function cn(...inputs: (string | undefined | null | boolean)[]) {
  return inputs.filter(Boolean).join(' ');
}
*/
