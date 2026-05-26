/**
 * components/Loading.tsx — Componentes de loading padronizados
 *
 * Fornece feedback visual consistente durante carregamento de dados.
 *
 * USO:
 *   import { LoadingSpinner, LoadingCard, LoadingSkeleton } from '@/components/Loading'
 *
 *   <LoadingSpinner size="md" />
 *   <LoadingCard message="Carregando casos..." />
 *   <LoadingSkeleton lines={3} />
 */

import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

// ═══════════════════════════════════════════════════════════════════════════════
// SPINNER SIMPLES
// ═══════════════════════════════════════════════════════════════════════════════

interface LoadingSpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

export function LoadingSpinner({ size = 'md', className }: LoadingSpinnerProps) {
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
  };

  return (
    <Loader2
      className={cn('animate-spin text-brand', sizeClasses[size], className)}
      aria-label="Carregando"
    />
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// CARD DE LOADING COM MENSAGEM
// ═══════════════════════════════════════════════════════════════════════════════

interface LoadingCardProps {
  message?: string;
  className?: string;
}

export function LoadingCard({ message = 'Carregando...', className }: LoadingCardProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-2xl border border-gray-200 bg-white px-6 py-12 text-center',
        className
      )}
    >
      <LoadingSpinner size="lg" />
      <p className="text-sm text-gray-500">{message}</p>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// SKELETON (animação de placeholder)
// ═══════════════════════════════════════════════════════════════════════════════

interface LoadingSkeletonProps {
  lines?: number;
  className?: string;
}

export function LoadingSkeleton({ lines = 1, className }: LoadingSkeletonProps) {
  return (
    <div className={cn('space-y-3', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded bg-gray-200"
          style={{ width: `${Math.random() * 30 + 70}%` }}
        />
      ))}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// LOADING INLINE (para botões e ações)
// ═══════════════════════════════════════════════════════════════════════════════

interface LoadingInlineProps {
  text?: string;
  className?: string;
}

export function LoadingInline({ text = 'Processando...', className }: LoadingInlineProps) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-sm text-gray-500', className)}>
      <LoadingSpinner size="sm" />
      {text}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// LOADING TABLE ROWS (skeleton para tabelas)
// ═══════════════════════════════════════════════════════════════════════════════

interface LoadingTableRowsProps {
  rows?: number;
  columns?: number;
}

export function LoadingTableRows({ rows = 3, columns = 4 }: LoadingTableRowsProps) {
  return (
    <>
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <tr key={rowIdx} className="border-b border-gray-100">
          {Array.from({ length: columns }).map((_, colIdx) => (
            <td key={colIdx} className="px-3 py-3">
              <div className="h-4 w-full animate-pulse rounded bg-gray-200" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
