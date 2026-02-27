'use client';
import { use } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Database, RefreshCw } from 'lucide-react';

interface FeatureSnapshot {
  player_id: string;
  feature_version: number;
  computed_at: string;
  txn_count_24h: number;
  txn_amount_24h: number;
  txn_count_7d: number;
  txn_amount_7d: number;
  txn_count_30d: number;
  txn_amount_30d: number;
  avg_txn_amount_30d: number;
  deposit_count_30d: number;
  withdrawal_count_30d: number;
  deposit_velocity?: number;
  unique_instruments_7d?: number;
  night_activity_ratio?: number;
  weekend_activity_ratio?: number;
  avg_odds_bet_7d?: number;
  win_loss_ratio_30d?: number;
  multi_currency_flag?: boolean;
  chargeback_rate_30d?: number;
  cashout_ratio_7d?: number;
  shared_instrument_score?: number;
}

type Props = { params: Promise<{ playerId: string }> };

const GROUPS = [
  {
    label: 'Volume Transacional',
    keys: ['txn_count_24h', 'txn_amount_24h', 'txn_count_7d', 'txn_amount_7d', 'txn_count_30d', 'txn_amount_30d', 'avg_txn_amount_30d'],
  },
  {
    label: 'Depósitos & Saques',
    keys: ['deposit_count_30d', 'withdrawal_count_30d', 'deposit_velocity', 'cashout_ratio_7d', 'chargeback_rate_30d'],
  },
  {
    label: 'Comportamento',
    keys: ['night_activity_ratio', 'weekend_activity_ratio', 'avg_odds_bet_7d', 'win_loss_ratio_30d', 'unique_instruments_7d'],
  },
  {
    label: 'Rede & Risco',
    keys: ['multi_currency_flag', 'shared_instrument_score'],
  },
];

const LABELS: Record<string, string> = {
  txn_count_24h: 'Transações (24h)', txn_amount_24h: 'Volume (24h)',
  txn_count_7d: 'Transações (7d)', txn_amount_7d: 'Volume (7d)',
  txn_count_30d: 'Transações (30d)', txn_amount_30d: 'Volume (30d)',
  avg_txn_amount_30d: 'Média por transação (30d)',
  deposit_count_30d: 'Depósitos (30d)', withdrawal_count_30d: 'Saques (30d)',
  deposit_velocity: 'Velocidade depósito', cashout_ratio_7d: 'Ratio saque (7d)',
  chargeback_rate_30d: 'Taxa chargeback (30d)',
  night_activity_ratio: 'Atividade noturna', weekend_activity_ratio: 'Atividade fim de semana',
  avg_odds_bet_7d: 'Odd média (7d)', win_loss_ratio_30d: 'Win/Loss (30d)',
  unique_instruments_7d: 'Instrumentos únicos (7d)',
  multi_currency_flag: 'Multi-moeda', shared_instrument_score: 'Score rede (instrumento)',
};

function fmt(key: string, val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'boolean') return val ? 'Sim' : 'Não';
  if (typeof val === 'number') {
    if (key.includes('ratio') || key.includes('rate') || key.includes('score')) {
      return (val as number).toFixed(4);
    }
    if (key.includes('amount') || key.includes('velocity')) {
      return `R$ ${(val as number).toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`;
    }
    return (val as number).toLocaleString('pt-BR');
  }
  return String(val);
}

export default function FeatureDetailPage({ params }: Props) {
  const { playerId } = use(params);

  const { data: current, isLoading: loadingCurrent, refetch } = useQuery({
    queryKey: ['features-current', playerId],
    queryFn: () => api.get<FeatureSnapshot>(`/players/${playerId}/features/current`).then((r) => r.data),
    retry: false,
  });

  const { data: history = [], isLoading: loadingHistory } = useQuery({
    queryKey: ['features-history', playerId],
    queryFn: () => api.get<FeatureSnapshot[]>(`/players/${playerId}/features`).then((r) => r.data),
    retry: false,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database size={22} className="text-brand" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Feature Store</h1>
            <p className="font-mono text-xs text-gray-400">{playerId}</p>
          </div>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
        >
          <RefreshCw size={14} /> Atualizar
        </button>
      </div>

      {/* Current features */}
      {loadingCurrent && <p className="text-sm text-gray-500">Carregando features atuais…</p>}

      {current && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <span>Versão: <strong className="text-gray-700">v{current.feature_version}</strong></span>
            <span>Atualizado: <strong className="text-gray-700">{new Date(current.computed_at).toLocaleString('pt-BR')}</strong></span>
          </div>

          {GROUPS.map((g) => (
            <div key={g.label} className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
              <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-600">{g.label}</span>
              </div>
              <div className="grid grid-cols-2 gap-0 sm:grid-cols-3 lg:grid-cols-4">
                {g.keys.map((k) => (
                  <div key={k} className="border-b border-r border-gray-50 px-4 py-3 last:border-r-0">
                    <p className="text-xs text-gray-400">{LABELS[k] ?? k}</p>
                    <p className="mt-0.5 text-sm font-semibold text-gray-800">
                      {fmt(k, (current as unknown as Record<string, unknown>)[k])}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* History table */}
      {history.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
          <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-600">
              Histórico de Snapshots ({history.length})
            </span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Data</th>
                <th className="px-4 py-2.5 text-right">Txn 24h</th>
                <th className="px-4 py-2.5 text-right">Volume 30d</th>
                <th className="px-4 py-2.5 text-right">Score Rede</th>
                <th className="px-4 py-2.5 text-right">Versão</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {history.slice(0, 30).map((h, i) => (
                <tr key={i} className="hover:bg-gray-50/50">
                  <td className="px-4 py-2 text-gray-500">{new Date(h.computed_at).toLocaleString('pt-BR')}</td>
                  <td className="px-4 py-2 text-right">{h.txn_count_24h}</td>
                  <td className="px-4 py-2 text-right">{fmt('amount', h.txn_amount_30d)}</td>
                  <td className="px-4 py-2 text-right">{h.shared_instrument_score?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2 text-right text-gray-400">v{h.feature_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loadingCurrent && !current && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <Database size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhum dado encontrado para este jogador</p>
        </div>
      )}
    </div>
  );
}
