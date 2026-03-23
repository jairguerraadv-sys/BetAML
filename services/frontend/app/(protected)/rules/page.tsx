'use client';

import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { BarChart, Bar, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fetchRules, simulateRule, SimulateRuleResult, Rule } from '@/lib/api';
import DataTable from '@/components/DataTable';

type SimMode = 'manual' | 'historical';

function pct(value?: number | null) {
  if (value == null) return '—';
  return `${(value * 100).toFixed(1)}%`;
}

export default function RulesPage() {
  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['rules'],
    queryFn: fetchRules,
  });

  const [selected, setSelected] = useState<Rule | null>(null);
  const [simMode, setSimMode] = useState<SimMode>('manual');
  const [simPayload, setSimPayload] = useState('{}');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [playerIds, setPlayerIds] = useState('');
  const [simResult, setSimResult] = useState<SimulateRuleResult | null>(null);

  const simulate = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error('Nenhuma regra selecionada');
      if (simMode === 'historical') {
        return simulateRule(selected.id, {
          from: dateFrom || undefined,
          to: dateTo || undefined,
          player_ids: playerIds
            .split(',')
            .map((v) => v.trim())
            .filter(Boolean),
        });
      }

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(simPayload);
      } catch {
        payload = {};
      }
      const body = Array.isArray(payload.events) ? payload : { events: [payload] };
      return simulateRule(selected.id, body);
    },
    onSuccess: (data) => setSimResult(data),
  });

  const columns = [
    { header: 'Nome', accessorKey: 'name' as keyof Rule },
    { header: 'Escopo', accessorKey: 'scope' as keyof Rule },
    { header: 'Severidade', accessorKey: 'severity' as keyof Rule },
    { header: 'Peso', accessorKey: 'weight' as keyof Rule },
    { header: 'Status', accessorKey: 'status' as keyof Rule },
    { header: 'Versão', accessorKey: 'version' as keyof Rule },
  ];

  const chartData = useMemo(() => simResult?.timeline ?? [], [simResult]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Regras DSL</h1>
        <p className="mt-1 text-sm text-gray-500">
          Simule com JSON manual ou use histórico real de alertas por período.
        </p>
      </div>

      <DataTable
        data={rules}
        columns={columns}
        loading={isLoading}
        onRowClick={(r) => {
          setSelected(r);
          setSimResult(null);
          setSimPayload('{}');
          setSimMode('manual');
        }}
      />

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-900">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold">{selected.name}</h2>
                <p className="text-xs text-gray-400">
                  Escopo: {selected.scope} · v{selected.version} · peso {selected.weight ?? 0.5}
                </p>
              </div>
              <button onClick={() => setSelected(null)} className="rounded-lg border px-3 py-1.5 text-sm">
                Fechar
              </button>
            </div>

            <div className="mb-4 grid gap-4 lg:grid-cols-[1.2fr_1fr]">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">Condição DSL</label>
                <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-800">
                  {selected.condition_dsl}
                </pre>
              </div>

              <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                <div className="mb-3 flex gap-2">
                  <button
                    onClick={() => setSimMode('manual')}
                    className={`rounded-lg px-3 py-1.5 text-sm ${simMode === 'manual' ? 'bg-brand text-white' : 'border'}`}
                  >
                    Manual
                  </button>
                  <button
                    onClick={() => setSimMode('historical')}
                    className={`rounded-lg px-3 py-1.5 text-sm ${simMode === 'historical' ? 'bg-brand text-white' : 'border'}`}
                  >
                    Histórico
                  </button>
                </div>

                {simMode === 'manual' ? (
                  <>
                    <label className="mb-1 block text-xs font-medium text-gray-600">
                      JSON de contexto
                    </label>
                    <textarea
                      rows={7}
                      value={simPayload}
                      onChange={(e) => setSimPayload(e.target.value)}
                      className="w-full rounded-lg border px-3 py-2 font-mono text-xs dark:bg-gray-800 dark:text-gray-200"
                      placeholder='{"transaction":{"amount":5000,"type":"DEPOSIT"},"features":{"deposit_sum_24h":10000},"player":{"pep_flag":true}}'
                    />
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-600">De</label>
                        <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
                      </div>
                      <div>
                        <label className="mb-1 block text-xs font-medium text-gray-600">Até</label>
                        <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800" />
                      </div>
                    </div>
                    <div>
                      <label className="mb-1 block text-xs font-medium text-gray-600">Player IDs</label>
                      <input
                        value={playerIds}
                        onChange={(e) => setPlayerIds(e.target.value)}
                        className="w-full rounded-lg border px-3 py-2 text-sm dark:bg-gray-800"
                        placeholder="player-1, player-2"
                      />
                    </div>
                  </div>
                )}

                <button
                  onClick={() => simulate.mutate()}
                  disabled={simulate.isPending}
                  className="mt-4 w-full rounded-lg bg-brand py-2 text-sm text-white disabled:opacity-50"
                >
                  {simulate.isPending ? 'Simulando...' : 'Executar simulação'}
                </button>
              </div>
            </div>

            {simResult && (
              <div className="space-y-4">
                <div className="grid gap-3 md:grid-cols-5">
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Alertas</p>
                    <p className="mt-1 text-xl font-semibold">{simResult.total_alerts ?? simResult.matches}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Players</p>
                    <p className="mt-1 text-xl font-semibold">{simResult.players?.length ?? 0}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Precisão est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.precision_estimated)}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Recall est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.recall_estimated)}</p>
                  </div>
                  <div className="rounded-xl border border-gray-100 p-4 dark:border-gray-800">
                    <p className="text-xs text-gray-400">Falso positivo est.</p>
                    <p className="mt-1 text-xl font-semibold">{pct(simResult.false_positive_estimated)}</p>
                  </div>
                </div>

                {chartData.length > 0 && (
                  <div className="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                    <h3 className="mb-3 text-sm font-semibold">Alertas por dia</h3>
                    <div className="h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                          <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                          <Tooltip />
                          <Bar dataKey="alerts" fill="#0f766e" radius={[6, 6, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}

                {simMode === 'manual' && simResult.results.length > 0 && (
                  <div className="rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
                    <h3 className="mb-3 text-sm font-semibold">Resultados do payload manual</h3>
                    <div className="space-y-2">
                      {simResult.results.map((result, idx) => (
                        <div key={idx} className={`rounded-lg border px-3 py-2 text-sm ${result.matched ? 'border-red-200 bg-red-50' : 'border-green-200 bg-green-50'}`}>
                          <p className="font-medium">{result.matched ? 'Disparou' : 'Não disparou'}</p>
                          {result.error && <p className="mt-1 text-xs text-orange-700">Erro: {result.error}</p>}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
