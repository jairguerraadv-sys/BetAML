'use client';
/**
 * ContextualHelp — botão "?" que, ao clicar, abre um painel com explicação
 * em linguagem simples sobre um conceito ou campo específico.
 *
 * Uso:
 *   <ContextualHelp id="sev-critical" title="O que é Crítico?">
 *     <p>Score acima de 95: requer ação imediata...</p>
 *   </ContextualHelp>
 */
import { useState, useId } from 'react';
import { HelpCircle, X } from 'lucide-react';

interface ContextualHelpProps {
  /** Título do painel de ajuda */
  title: string;
  /** Conteúdo do painel — pode ser JSX livre */
  children: React.ReactNode;
  /** Tamanho do ícone (px) */
  iconSize?: number;
  /** Posicionamento do painel: 'right' (padrão) ou 'left' */
  side?: 'right' | 'left';
}

export default function ContextualHelp({
  title,
  children,
  iconSize = 14,
  side = 'right',
}: ContextualHelpProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  return (
    <span className="relative inline-flex items-center">
      <button
        type="button"
        aria-label={`Ajuda: ${title}`}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="ml-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-gray-400 transition-colors hover:bg-brand/10 hover:text-brand focus:outline-none focus:ring-2 focus:ring-brand"
      >
        <HelpCircle size={iconSize} />
      </button>

      {open && (
        <>
          {/* Overlay para fechar ao clicar fora */}
          <span
            className="fixed inset-0 z-40"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div
            id={panelId}
            role="tooltip"
            className={`absolute top-6 z-50 w-64 rounded-xl border border-blue-100 bg-white shadow-xl ${
              side === 'left' ? 'right-0' : 'left-0'
            }`}
          >
            {/* Cabeçalho */}
            <div className="flex items-center justify-between rounded-t-xl border-b border-blue-50 bg-blue-50 px-4 py-2.5">
              <p className="text-xs font-bold text-blue-800">{title}</p>
              <button
                onClick={() => setOpen(false)}
                aria-label="Fechar ajuda"
                className="text-blue-400 hover:text-blue-700"
              >
                <X size={13} />
              </button>
            </div>
            {/* Corpo */}
            <div className="p-4 text-xs leading-relaxed text-gray-600">{children}</div>
          </div>
        </>
      )}
    </span>
  );
}
