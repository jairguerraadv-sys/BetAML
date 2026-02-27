'use client';
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import DataTable from '@/components/DataTable';

interface MappingConfig {
  id: string;
  source_system: string;
  entity_type: string;
  version: number;
  is_active: boolean;
  description?: string;
}

interface TestResult {
  output: Record<string, unknown>;
  errors: string[];
}

export default function MappingsPage() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<MappingConfig | null>(null);
  const [sampleJson, setSampleJson] = useState('{}');
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);

  const { data: mappings = [], isLoading } = useQuery({
    queryKey: ['mappings'],
    queryFn: () => api.get<MappingConfig[]>('/mappings').then((r) => r.data),
  });

  async function runTest() {
    if (!selected) return;
    setTestLoading(true);
    setTestResult(null);
    try {
      let sample: object = {};
      try { sample = JSON.parse(sampleJson); } catch { /* ok */ }
      const res = await api.post<TestResult>(`/mappings/${selected.id}/test`, { sample });
      setTestResult(res.data);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } };
      setTestResult({ output: {}, errors: [err?.response?.data?.detail ?? 'Erro desconhecido'] });
    } finally {
      setTestLoading(false);
    }
  }

  const columns = [
    { header: 'Source System',  accessorKey: 'source_system' as keyof MappingConfig },
    { header: 'Entity Type',    accessorKey: 'entity_type' as keyof MappingConfig },
    { header: 'Versão',         accessorKey: 'version' as keyof MappingConfig },
    {
      header: 'Ativo',
      accessorKey: 'is_active' as keyof MappingConfig,
      cell: (v: unknown) => (v as boolean)
        ? <span className="rounded px-2 py-0.5 text-xs bg-green-100 text-green-700">Sim</span>
        : <span className="rounded px-2 py-0.5 text-xs bg-gray-100 text-gray-500">Não</span>,
    },
    { header: 'Descrição',      accessorKey: 'description' as keyof MappingConfig },
  ];

  return (
    <div>
      <h1 className="mb-6 text-2xl font-bold">Conectores (MappingConfig)</h1>

      <DataTable
        data={mappings}
        columns={columns}
        loading={isLoading}
        onRowClick={(m) => { setSelected(m); setTestResult(null); setSampleJson('{}'); }}
      />

      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="mb-1 text-lg font-semibold">Testar Mapping</h2>
            <p className="mb-3 text-xs text-gray-400">
              {selected.source_system} → {selected.entity_type} · v{selected.version}
            </p>

            <label className="mb-1 block text-xs font-medium text-gray-600">
              Payload bruto de exemplo (JSON)
            </label>
            <textarea
              rows={6}
              value={sampleJson}
              onChange={(e) => setSampleJson(e.target.value)}
              className="mb-3 w-full rounded-lg border px-3 py-2 font-mono text-xs"
              placeholder='{"transactionId": "T001", "amount": "1500.00", ...}'
            />

            {testResult && (
              <div className="mb-3">
                {testResult.errors?.length > 0 ? (
                  <div className="rounded-lg bg-red-50 px-4 py-3 text-xs text-red-700">
                    <p className="font-semibold mb-1">Erros:</p>
                    <ul className="list-disc list-inside">
                      {testResult.errors.map((e, i) => <li key={i}>{e}</li>)}
                    </ul>
                  </div>
                ) : (
                  <div className="rounded-lg bg-gray-50 p-3">
                    <p className="mb-1 text-xs font-semibold text-gray-700">Resultado canônico:</p>
                    <pre className="overflow-x-auto text-xs text-gray-600">
                      {JSON.stringify(testResult.output, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-3">
              <button
                onClick={runTest}
                disabled={testLoading}
                className="flex-1 rounded-lg bg-brand py-2 text-sm text-white disabled:opacity-50"
              >
                {testLoading ? 'Testando...' : 'Testar'}
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
