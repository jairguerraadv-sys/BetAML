'use client';
import { use, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  fetchFeatureStoreCurrent,
  fetchFeatureStoreHistory,
  fetchFeaturePopulationStats,
  type FeatureStoreCurrent,
  type FeatureStoreHistory,
  type FeaturePopulationStats,
} from '@/lib/api';
import { BarChart2, Database, RefreshCw } from 'lucide-react';

type Props = { params: Promise<{ playerId: string }> };

// ── Feature groups for current tab ───────────────────────────────────────────

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

// ── Tab types ─────────────────────────────────────────────────────────────────

type Tab = 'Atuais' | 'Histórico' | 'Distribuição';
const TABS: Tab[] = ['Atuais', 'Histórico', 'Distribuição'];

// ── DriftBadge ────────────────────────────────────────────────────────────────

function DriftBadge({ score }: { score?: number | null }) {
  if (score == null) {
    return (
      <span className="inline-flex rounded px-2 py-0.5 text-xs bg-gray-100 text-gray-500">
        —
      </span>
    );
  }
  const cls =
    score < 0.3
      ? 'bg-green-100 text-green-700'
      : score <= 0.6
        ? 'bg-yellow-100 text-yellow-700'
        : 'bg-red-100 text-red-700';
  return (
    <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {score.toFixed(4)}
    </span>
  );
}

// ── Page component ────────────────────────────────────────────────────────────

export default function FeatureDetailPage({ params }: Props) {
  const { playerId } = use(params);

  const [activeTab, setActiveTab] = useState<Tab>('Atuais');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [statsFilter, setStatsFilter] = useState('');

  const { data: current, isLoading: loadingCurrent, refetch } = useQuery({
    queryKey: ['features-current', playerId],
    queryFn: () => fetchFeatureStoreCurrent(playerId),
    retry: false,
  });

  const { data: history, isLoading: loadingHistory } = useQuery({
    queryKey: ['features-history', playerId, dateFrom, dateTo],
    queryFn: () =>
      fetchFeatureStoreHistory(playerId, {
        from: dateFrom || undefined,
        to: dateTo || undefined,
      }),
    retry: false,
  });

  const { data: popStats, isLoading: loadingStats } = useQuery({
    queryKey: ['feature-population-stats'],
    queryFn: fetchFeaturePopulationStats,
    retry: false,
    enabled: activeTab === 'Distribuição',
  });

  const currentFeatures = current?.features ?? {};
  const historyItems = history?.items ?? [];
  const filteredStats = popStats
    ? Object.entries(popStats.features).filter(([k]) =>
        k.toLowerCase().includes(statsFilter.toLowerCase()),
      )
    : [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database size={22} className="text-brand" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Feature Store</h1>
            <p className="font-mono text-xs text-gray-400">{playerId}</p>
          </div>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300"
        >
          <RefreshCw size={14} /> Atualizar
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-0 border-b border-gray-200 dark:border-gray-700">
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-5 py-2.5 text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'border-b-2 border-brand text-brand'
                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* ── Tab: Atuais ─────────────────────────────────────────────────────── */}
      {activeTab === 'Atuais' && (
        <>
          {loadingCurrent && <p className="text-sm text-gray-500">Carregando features atuais…</p>}

          {current && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 text-xs text-gray-400">
                <span>
                  Versão:{' '}
                  <strong className="text-gray-700 dark:text-gray-300">
                    v{current.feature_version}
                  </strong>
                </span>
                <span>
                  Atualizado:{' '}
                  <strong className="text-gray-700 dark:text-gray-300">
                    {current.computed_at
                      ? new Date(current.computed_at).toLocaleString('pt-BR')
                      : '—'}
                  </strong>
                </span>
              </div>

              {GROUPS.map((g) => (
                <div
                  key={g.label}
                  className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900"
                >
                  <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5 dark:border-gray-700 dark:bg-gray-800">
                    <span className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
                      {g.label}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-0 sm:grid-cols-3 lg:grid-cols-4">
                    {g.keys.map((k) => (
                      <div
                        key={k}
                        className="border-b border-r border-gray-50 px-4 py-3 last:border-r-0 dark:border-gray-800"
                      >
                        <p className="text-xs text-gray-400">{LABELS[k] ?? k}</p>
                        <p className="mt-0.5 text-sm font-semibold text-gray-800 dark:text-gray-200">
                          {fmt(k, currentFeatures[k])}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {!loadingCurrent && !current && (
            <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center dark:border-gray-700">
              <Database size={32} className="mx-auto mb-3 text-gray-300" />
              <p className="text-sm text-gray-500">Nenhum dado encontrado para este jogador</p>
            </div>
          )}
        </>
      )}

      {/* ── Tab: Histórico ──────────────────────────────────────────────────── */}
      {activeTab === 'Histórico' && (
        <div className="space-y-4">
          {/* Date range filter */}
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs font-medium text-gray-500">De:</label>
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="rounded border border-gray-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            />
            <label className="text-xs font-medium text-gray-500">Até:</label>
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="rounded border border-gray-200 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
            />
          </div>

          {loadingHistory && <p className="text-sm text-gray-500">Carregando histórico…</p>}

          {!loadingHistory && historyItems.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
              <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5 dark:border-gray-700 dark:bg-gray-800">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400">
                  Histórico de Snapshots ({historyItems.length})
                </span>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500 dark:bg-gray-800/50 dark:text-gray-400">
                  <tr>
                    <th className="px-4 py-2.5 text-left">Data</th>
                    <th className="px-4 py-2.5 text-right">Depósitos 24h</th>
                    <th className="px-4 py-2.5 text-right">Volume 30d</th>
                    <th className="px-4 py-2.5 text-right">Score Instrumento</th>
                    <th className="px-4 py-2.5 text-center">Drift Score</th>
                    <th className="px-4 py-2.5 text-right">Versão</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
                  {historyItems.slice(0, 30).map((item) => (
                    <tr key={item.id} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/50">
                      <td className="px-4 py-2 text-gray-500 dark:text-gray-400">
                        {item.snapshot_date
                          ? new Date(item.snapshot_date).toLocaleDateString('pt-BR')
                          : new Date(item.created_at).toLocaleString('pt-BR')}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {fmt('deposit_count_24h', item.features.deposit_count_24h)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {fmt('deposit_sum_30d', item.features.deposit_sum_30d)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {fmt('shared_instrument_score', item.features.shared_instrument_score)}
                      </td>
                      <td className="px-4 py-2 text-center">
                        <DriftBadge score={item.drift_score} />
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">
                        v{item.feature_version ?? 1}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!loadingHistory && historyItems.length === 0 && (
            <p className="text-sm text-gray-500">
              Nenhum snapshot encontrado para o período selecionado.
            </p>
          )}
        </div>
      )}

      {/* ── Tab: Distribuição ───────────────────────────────────────────────── */}
      {activeTab === 'Distribuição' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <BarChart2 size={16} className="text-gray-400" />
            <span className="text-xs text-gray-500">
              Estatísticas calculadas sobre todos os jogadores do tenant — job diário às 06:00 UTC.
            </span>
          </div>

          <input
            type="text"
            value={statsFilter}
            onChange={(e) => setStatsFilter(e.target.value)}
            placeholder="Filtrar feature pelo nome…"
            className="w-full max-w-xs rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand dark:border-gray-700 dark:bg-gray-800 dark:text-white"
          />

          {loadingStats && (
            <p className="text-sm text-gray-500">Carregando estatísticas de população…</p>
          )}

          {!loadingStats && filteredStats.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
              <table className="w-full text-sm">
                <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500 dark:bg-gray-800/50 dark:text-gray-400">
                  <tr>
                    <th className="px-4 py-2.5 text-left">Feature</th>
                    <th className="px-4 py-2.5 text-right">N</th>
                    <th className="px-4 py-2.5 text-right">Média</th>
                    <th className="px-4 py-2.5 text-right">Mediana</th>
                    <th className="px-4 py-2.5 text-right">Desvio</th>
                    <th className="px-4 py-2.5 text-right">P10</th>
                    <th className="px-4 py-2.5 text-right">P90</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50 dark:divide-gray-800">
                  {filteredStats.map(([key, stat]) => (
                    <tr key={key} className="hover:bg-gray-50/50 dark:hover:bg-gray-800/50">
                      <td className="px-4 py-2 font-mono text-xs text-gray-700 dark:text-gray-300">
                        {key}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-400">{stat.count}</td>
                      <td className="px-4 py-2 text-right">{stat.mean.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right">{stat.p50.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right">{stat.std.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right text-gray-400">{stat.p10.toFixed(4)}</td>
                      <td className="px-4 py-2 text-right text-gray-400">{stat.p90.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {popStats?.computed_at && (
                <div className="border-t border-gray-50 px-4 py-2 dark:border-gray-800">
                  <p className="text-xs text-gray-400">
                    Calculado em:{' '}
                    {new Date(popStats.computed_at).toLocaleString('pt-BR')}
                  </p>
                </div>
              )}
            </div>
          )}

          {!loadingStats && !popStats && (
            <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center dark:border-gray-700">
              <BarChart2 size={32} className="mx-auto mb-3 text-gray-300" />
              <p className="text-sm text-gray-500">
                Estatísticas de população não disponíveis.
              </p>
              <p className="mt-1 text-xs text-gray-400">
                O job de cálculo executa diariamente às 06:00 UTC.
              </p>
            </div>
          )}

          {!loadingStats && popStats && filteredStats.length === 0 && statsFilter && (
            <p className="text-sm text-gray-500">
              Nenhuma feature encontrada para &ldquo;{statsFilter}&rdquo;.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
