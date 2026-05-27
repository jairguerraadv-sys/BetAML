'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Search, X, FileText, AlertTriangle, Users } from 'lucide-react';
import { api } from '@/lib/api';
import { useLocale } from '@/lib/i18n';
import { useGlossary } from '@/lib/use-glossary';

interface SearchResult {
  type: 'player' | 'case' | 'alert';
  id: string;
  label: string;
  sublabel: string;
  href: string;
}

interface SearchResponse {
  players: Array<{ id: string; external_id: string; name: string; cpf_masked?: string; risk_band: string }>;
  cases: Array<{ id: string; reference_number: string; title: string; status: string }>;
  alerts: Array<{ id: string; alert_type: string; severity: string; player_id: string }>;
}

const ICONS = {
  player: Users,
  case: FileText,
  alert: AlertTriangle,
};

const TYPE_LABELS = { player: 'Jogador', case: 'Caso', alert: 'Alerta' };

export default function GlobalSearch() {
  const router = useRouter();
  const [locale] = useLocale();
  const { translate } = useGlossary();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  // Cmd+K / Ctrl+K opens search
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setOpen(true);
      }
      if (e.key === 'Escape') setOpen(false);
    }
    function onOpenRequest() {
      setOpen(true);
    }
    window.addEventListener('keydown', onKey);
    window.addEventListener('betaml:open-global-search', onOpenRequest);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('betaml:open-global-search', onOpenRequest);
    };
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  const runSearch = useCallback(async (q: string) => {
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.get<SearchResponse>(`/search?q=${encodeURIComponent(q)}`);
      const mapped: SearchResult[] = [
        ...data.players.map((p) => ({
          type: 'player' as const,
          id: p.id,
          label: p.name || p.external_id,
          sublabel: `${translate.riskBand(p.risk_band)} · ${p.external_id}${p.cpf_masked ? ` · ${p.cpf_masked}` : ''}`,
          href: `/players/${p.id}`,
        })),
        ...data.cases.map((c) => ({
          type: 'case' as const,
          id: c.id,
          label: c.reference_number,
          sublabel: `${translate.caseStatus(c.status)} · ${c.title}`,
          href: `/cases/${c.id}`,
        })),
        ...data.alerts.map((a) => ({
          type: 'alert' as const,
          id: a.id,
          label: translate.alertType(a.alert_type),
          sublabel: `${translate.severity(a.severity)} · ${a.id.slice(0, 8)}`,
          href: `/alerts/${a.id}`,
        })),
      ];
      setResults(mapped);
      setCursor(0);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  function handleInput(e: React.ChangeEvent<HTMLInputElement>) {
    const q = e.target.value;
    setQuery(q);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(q), 300);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === 'Enter' && results[cursor]) {
      navigate(results[cursor]);
    }
  }

  function navigate(r: SearchResult) {
    router.push(r.href);
    setOpen(false);
    setQuery('');
    setResults([]);
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-20 backdrop-blur-sm"
      onClick={() => setOpen(false)}
    >
      <div
        className="w-full max-w-xl rounded-2xl bg-white shadow-2xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Busca global"
      >
        {/* Input */}
        <div className="flex items-center gap-3 border-b border-gray-200 px-4 py-3 dark:border-gray-700">
          <Search size={18} className="shrink-0 text-gray-400" />
          <input
            ref={inputRef}
            value={query}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Buscar CPF, jogador, caso, alerta…"
            aria-label="Buscar CPF, nome, caso ou alerta"
            className="flex-1 bg-transparent text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none dark:text-gray-100"
          />
          {query && (
            <button
              onClick={() => { setQuery(''); setResults([]); }}
              aria-label="Limpar busca"
            >
              <X size={15} className="text-gray-400 hover:text-gray-600" />
            </button>
          )}
          <kbd className="rounded border border-gray-200 bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500 dark:border-gray-600 dark:bg-gray-800">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto py-1" role="listbox" aria-label="Resultados da busca global">
          {loading && (
            <p className="px-4 py-3 text-sm text-gray-400">Buscando…</p>
          )}
          {!loading && query.length >= 2 && results.length === 0 && (
            <p className="px-4 py-3 text-sm text-gray-400">Nenhum resultado para <span className="font-medium">{query}</span></p>
          )}
          {results.map((r, i) => {
            const Icon = ICONS[r.type];
            return (
              <button
                key={r.id}
                onClick={() => navigate(r)}
                role="option"
                aria-selected={i === cursor}
                className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                  i === cursor
                    ? 'bg-brand/10 dark:bg-brand/20'
                    : 'hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gray-100 dark:bg-gray-700">
                  <Icon size={13} className="text-gray-500 dark:text-gray-400" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                    {r.label}
                  </p>
                  <p className="truncate text-xs text-gray-500 dark:text-gray-400">{r.sublabel}</p>
                </div>
                <span className="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500 dark:bg-gray-700 dark:text-gray-400">
                  {TYPE_LABELS[r.type]}
                </span>
              </button>
            );
          })}
          {!loading && results.length > 0 && (
            <p className="px-4 py-1.5 text-[10px] text-gray-400">
              {locale === 'en-US' ? 'Use ↑↓ to navigate · Enter to open' : '↑↓ navegar · Enter selecionar'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
