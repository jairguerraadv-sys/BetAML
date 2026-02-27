'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { BrainCircuit, Trophy, BarChart2 } from 'lucide-react';

interface ModelEntry {
  id: string;
  model_type: string;
  version: string;
  status: 'champion' | 'challenger' | 'archived' | 'active';
  metrics: Record<string, number>;
  trained_at: string;
  promoted_at?: string;
}

const fetchModels = () =>
  api.get<ModelEntry[]>('/model-registry').then((r) => r.data);

const promoteModel = (id: string) =>
  api.post(`/model-registry/${id}/promote`);

const STATUS_BADGE: Record<string, string> = {
  champion:   'bg-yellow-100 text-yellow-700',
  challenger: 'bg-blue-100 text-blue-700',
  active:     'bg-green-100 text-green-700',
  archived:   'bg-gray-100 text-gray-500',
};

export default function ModelRegistryPage() {
  const qc = useQueryClient();

  const { data: models = [], isLoading } = useQuery({
    queryKey: ['model-registry'],
    queryFn: fetchModels,
  });

  const promote = useMutation({
    mutationFn: promoteModel,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-registry'] }),
  });

  // Group by model_type
  const byType = models.reduce<Record<string, ModelEntry[]>>((acc, m) => {
    (acc[m.model_type] ??= []).push(m);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <BrainCircuit size={22} className="text-brand" />
        <h1 className="text-2xl font-bold text-gray-900">Registro de Modelos ML</h1>
      </div>

      {isLoading && <p className="text-sm text-gray-500">Carregando…</p>}

      {Object.entries(byType).map(([type, entries]) => (
        <section key={type} className="overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm">
          <div className="flex items-center gap-2 border-b border-gray-100 bg-gray-50 px-4 py-2.5">
            <BarChart2 size={15} className="text-gray-500" />
            <span className="text-xs font-semibold uppercase tracking-wide text-gray-600">{type}</span>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-gray-50/50 text-xs font-semibold uppercase text-gray-500">
              <tr>
                <th className="px-4 py-2.5 text-left">Versão</th>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-left">AUC-ROC</th>
                <th className="px-4 py-2.5 text-left">Precision</th>
                <th className="px-4 py-2.5 text-left">Recall</th>
                <th className="px-4 py-2.5 text-left">Treinado em</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {entries.map((m) => (
                <tr key={m.id} className={`hover:bg-gray-50/50 ${m.status === 'champion' ? 'bg-yellow-50/30' : ''}`}>
                  <td className="px-4 py-2.5 font-medium">{m.version}</td>
                  <td className="px-4 py-2.5">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${STATUS_BADGE[m.status] ?? 'bg-gray-100'}`}>
                      {m.status === 'champion' && <Trophy size={10} />}
                      {m.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">{m.metrics?.auc_roc?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5">{m.metrics?.precision?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5">{m.metrics?.recall?.toFixed(4) ?? '—'}</td>
                  <td className="px-4 py-2.5 text-gray-500">{new Date(m.trained_at).toLocaleDateString('pt-BR')}</td>
                  <td className="px-4 py-2.5 text-right">
                    {m.status !== 'champion' && (
                      <button
                        onClick={() => promote.mutate(m.id)}
                        disabled={promote.isPending}
                        className="rounded px-2 py-1 text-xs font-medium text-brand hover:underline disabled:opacity-50"
                      >
                        Promover
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {!isLoading && models.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-200 py-16 text-center">
          <BrainCircuit size={32} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm text-gray-500">Nenhum modelo registrado</p>
        </div>
      )}
    </div>
  );
}
