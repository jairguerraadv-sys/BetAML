'use client';
import { use } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchFeatureStoreCurrent, fetchFeatureStoreHistory, type FeatureStoreCurrent, type FeatureStoreHistory } from '@/lib/api';
import { Database, RefreshCw } from 'lucide-react';

type Props = { params: Promise<{ playerId: string }> };

const GROUPS = [
  {
    label: 'Volume Transacional',
    keys: ['deposit_count_24h', 'deposit_count_7d', 'deposit_sum_24h', 'deposit_sum_7d', 'deposit_sum_30d', 'deposit_velocity'],
  },
  {
    label: 'Depósitos & Saques',
    keys: ['withdrawal_count_24h', 'withdrawal_sum_24h', 'withdrawal_sum_7d', 'cashout_ratio_7d', 'chargeback_count_30d', 'chargeback_rate_30d'],
  },
  {
    label: 'Comportamento',
    keys: ['night_activity_ratio', 'weekend_activity_ratio', 'avg_odds_bet_7d', 'win_loss_ratio_30d', 'unique_instruments_7d', 'avg_deposit_to_withdrawal_hours', 'bonus_to_real_ratio_30d'],
  },
  {
    label: 'Rede & Risco',
    keys: ['multi_currency_flag', 'shared_instrument_score', 'cluster_size', 'cluster_id'],
  },
];

const LABELS: Record<string, string> = {
  deposit_count_24h: 'Depósitos (24h)',
  deposit_count_7d: 'Depósitos (7d)',
  deposit_sum_24h: 'Volume depósitos (24h)',
  deposit_sum_7d: 'Volume depósitos (7d)',
  deposit_sum_30d: 'Volume depósitos (30d)',
  deposit_velocity: 'Velocidade depósito',
  withdrawal_count_24h: 'Saques (24h)',
  withdrawal_sum_24h: 'Volume saques (24h)',
  withdrawal_sum_7d: 'Volume saques (7d)',
  cashout_ratio_7d: 'Ratio saque (7d)',
  chargeback_count_30d: 'Chargebacks (30d)',
  chargeback_rate_30d: 'Taxa chargeback (30d)',
  night_activity_ratio: 'Atividade noturna',
  weekend_activity_ratio: 'Atividade fim de semana',
  avg_odds_bet_7d: 'Odd média (7d)',
  win_loss_ratio_30d: 'Win/Loss (30d)',
  unique_instruments_7d: 'Instrumentos únicos (7d)',
  avg_deposit_to_withdrawal_hours: 'Tempo méd. depósito->saque',
  bonus_to_real_ratio_30d: 'Ratio bônus/dinheiro real (30d)',
  multi_currency_flag: 'Multi-moeda',
  shared_instrument_score: 'Score rede (instrumento)',
  cluster_size: 'Tamanho do cluster',
  cluster_id: 'Cluster ID',
};

function fmt(key: string, val: unknown): string {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'boolean') return val ? 'Sim' : 'Não';
  if (typeof val === 'number') {
    if (key.includes('ratio') || key.includes('rate') || key.includes('score')) {
      return (val as number).toFixed(4);
    }
    if (key.includes('amount') || key.includes('sum') || key.includes('velocity')) {
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
    queryFn: () => fetchFeatureStoreCurrent(playerId),
    retry: false,
  });

  const { data: history, isLoading: loadingHistory } = useQuery({
    queryKey: ['features-history', playerId],
    queryFn: () => fetchFeatureStoreHistory(playerId),
    retry: false,
  });

  const currentFeatures = current?.features ?? {};
  const historyItems = history?.items ?? [];

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
            <span>Atualizado: <strong className="text-gray-700">{current.computed_at ? new Date(current.computed_at).toLocaleString('pt-BR') : '—'}</strong></span>
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
                      {fmt(k, currentFeatures[k])}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* History table */}
      {!loadingHistory && historyItems.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
          <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-600">
              Histórico de Snapshots ({historyItems.length})
            </span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Data</th>
                <th className="px-4 py-2.5 text-right">Depósitos 24h</th>
                <th className="px-4 py-2.5 text-right">Volume 30d</th>
                <th className="px-4 py-2.5 text-right">Score Instrumento</th>
                <th className="px-4 py-2.5 text-right">Versão</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {historyItems.slice(0, 30).map((item) => (
                <tr key={item.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-2 text-gray-500">{item.snapshot_date ? new Date(item.snapshot_date).toLocaleDateString('pt-BR') : new Date(item.created_at).toLocaleString('pt-BR')}</td>
                  <td className="px-4 py-2 text-right">{fmt('deposit_count_24h', item.features.deposit_count_24h)}</td>
                  <td className="px-4 py-2 text-right">{fmt('deposit_sum_30d', item.features.deposit_sum_30d)}</td>
                  <td className="px-4 py-2 text-right">{fmt('shared_instrument_score', item.features.shared_instrument_score)}</td>
                  <td className="px-4 py-2 text-right text-gray-400">v{item.feature_version ?? 1}</td>
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
