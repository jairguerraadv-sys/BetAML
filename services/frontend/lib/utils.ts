/**
 * lib/utils.ts — Utilitários gerais
 */

/**
 * Merge de classes CSS (Tailwind) com suporte a condicionais
 *
 * Uso:
 *   cn('base-class', condition && 'conditional-class', className)
 */
export function cn(...inputs: (string | undefined | null | boolean | 0)[]) {
  return inputs.filter(Boolean).join(' ');
}

/**
 * Formata número como moeda brasileira
 */
export function formatCurrency(value: number | null | undefined): string {
  if (value == null) return 'R$ 0,00';
  return new Intl.NumberFormat('pt-BR', {
    style: 'currency',
    currency: 'BRL',
  }).format(value);
}

/**
 * Formata porcentagem
 */
export function formatPercent(value: number | null | undefined): string {
  if (value == null) return '0%';
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * Trunca texto com elipsis
 */
export function truncate(str: string, maxLength: number): string {
  if (str.length <= maxLength) return str;
  return `${str.slice(0, maxLength)}…`;
}
