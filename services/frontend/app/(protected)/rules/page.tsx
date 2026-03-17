'use client';
import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { fetchRules, simulateRule, SimulateRuleResult, Rule } from '@/lib/api';
import DataTable from '@/components/DataTable';

export default function RulesPage() {
  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['rules'],
    queryFn:  fetchRules,
  });
  const [selected, setSelected]    = useState<Rule | null>(null);
  const [simPayload, setSimPayload] = useState('{}');
  const [simResult, setSimResult]  = useState<SimulateRuleResult | null>(null);

  const simulate = useMutation({
    mutationFn: () => {
      let payload: object;
      try { payload = JSON.parse(simPayload); }
      catch { payload = {}; }
      // Wrap in events array if the user didn't already do it
      const body = Array.isArray((payload as Record<string, unknown>).events)
        ? payload
        : { events: [payload] };
      return simulateRule(selected!.id, body);
    },
    onSuccess: (data) => setSimResult(data),
  });

  const SEV: Record<string, string> = {
    CRITICAL: 'bg-red-100 text-red-700',
    HIGH:     'bg-orange-100 text-orange-700',
    MEDIUM:   'bg-yellow-100 text-yellow-700',
    LOW:      'bg-green-100 text-green-700',
  };

  const columns = [
    { header: 'Nome',       accessorKey: 'name' as keyof Rule },
    { header: 'Escopo',     accessorKey: 'scope' as keyof Rule },
    {
      header: 'Severidade',
      accessorKey: 'severity' as keyof Rule,
      cell: (v: unknown) => {
        const s = v as string;
        return (
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${SEV[s] ?? 'bg-gray-100'}`}>
            {s}
          </span>
        );
      },
    },
    { header: 'Status',  accessorKey: 'status' as keyof Rule },
    { header: 'Versão',  accessorKey: 'version' as keyof Rule },
  ];

  const firstResult = simResult?.results?.[0];
  const fired = (simResult?.matches ?? 0) > 0;

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Regras DSL</h1>
      <DataTable
        data={rules}
        columns={columns}
        loading={isLoading}
        onRowClick={(r) => { setSelected(r); setSimResult(null); setSimPayload('{}'); }}
      />

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-900">
            <h2 className="mb-1 text-lg font-semibold">{selected.name}</h2>
            <p className="mb-3 text-xs text-gray-400">Escopo: {selected.scope} · v{selected.version}</p>

            <label className="mb-1 block text-xs font-medium text-gray-600">Condição DSL</label>
            <pre className="mb-4 overflow-x-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-800">
              {selected.condition_dsl}
            </pre>

            <label className="mb-1 block text-xs font-medium text-gray-600">
              Contexto de simulação (JSON com campos <code>transaction</code>, <code>features</code>, <code>player</code>, <code>params</code>)
            </label>
            <textarea
              rows={5}
              value={simPayload}
              onChange={(e) => setSimPayload(e.target.value)}
              className="mb-3 w-full rounded-lg border px-3 py-2 font-mono text-xs dark:bg-gray-800 dark:text-gray-200"
              placeholder='{"transaction": {"amount": 5000, "type": "DEPOSIT"}, "features": {"deposit_sum_24h": 10000}}'
            />

            {simResult && (
              <div className={`mb-3 rounded-lg px-4 py-3 text-sm font-medium ${
                fired ? 'bg-red-50 text-red-700' : 'bg-green-50 text-green-700'
              }`}>
                {fired
                  ? `⚠ Regra DISPAROU (${simResult.matches} de ${simResult.results.length} evento(s))`
                  : '✓ Regra NÃO disparou'}
                {firstResult?.error && (
                  <p className="mt-1 text-xs font-normal text-orange-700">
                    Aviso: {firstResult.error}
                  </p>
                )}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={() => simulate.mutate()}
                disabled={simulate.isPending}
                className="flex-1 rounded-lg bg-brand py-2 text-sm text-white disabled:opacity-50"
              >
                {simulate.isPending ? 'Simulando...' : 'Simular'}
              </button>
              <button
                onClick={() => setSelected(null)}
                className="flex-1 rounded-lg border py-2 text-sm"
              >
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
